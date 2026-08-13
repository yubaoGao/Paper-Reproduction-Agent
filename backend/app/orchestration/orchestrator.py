"""Production reproduction orchestration over Task 09/10 runtime ports."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from backend.app.curie_core.reproduction import (
    analyzer_interpret,
    architect_plan,
    concluder_decide,
    scheduler_partition,
    technician_command,
)
from backend.app.domain import (
    ArtifactReference,
    AttemptRecord,
    AttemptStatus,
    FailureCategory,
    ExperimentActionType,
    ResultAggregation,
    ResourceAdaptationOutcome,
    PatchStatus,
    ReproductionExecutionPlan,
    ReproductionRun,
    RunStatus,
    StepStatus,
    ValidationPhase,
    ValidationRecord,
)
from backend.app.services.result_resolution import ResultResolutionRequest,aggregate_final_result
from backend.app.runtime.curie_models import (
    CommandExecutionRequest,
    CommandExecutionResult,
    CuriePlanRecord,
    CurieValidationRecord,
    ConstraintLevel,
    ExecutionStatus,
)
from backend.app.runtime.guard import ExperimentSpecificationGuard

from .context import PlanStepContextFactory
from .dispatcher import ExecutionDispatcher
from .failure import FailureClassifier
from .manifest import create_reproduction_run, verify_plan
from .patching import PatchCoordinator
from .ports import SemanticValidationRequest
from .retry import RetryPolicy
from .resource_adaptation import ResourceWaitRequired
from .state_machine import RunStateMachine
from .validation import DeterministicValidator


class WorkspaceCleanupError(RuntimeError):
    pass


class ReproductionOrchestrator:
    """Synchronous application service; queueing and workers remain out of scope."""

    def __init__(
        self,
        *,
        repository,
        command_port,
        workspace_port,
        artifact_port,
        semantic_validation_port=None,
        coding_port=None,
        cancellation_port=None,
        retry_policy=None,
        deterministic_validator=None,
        failure_classifier=None,
        context_factory=None,
        result_resolver=None,
        resource_adaptation_port=None,
    ) -> None:
        self.repository = repository
        self.command_port = command_port
        self.workspace_port = workspace_port
        self.artifact_port = artifact_port
        self.semantic_validation_port = semantic_validation_port
        self.cancellation_port = cancellation_port
        self.retry_policy = retry_policy or RetryPolicy()
        self.validator = deterministic_validator or DeterministicValidator()
        self.classifier = failure_classifier or FailureClassifier()
        self.context_factory = context_factory or PlanStepContextFactory()
        self.result_resolver = result_resolver
        self.resource_adaptation_port = resource_adaptation_port
        self.state_machine = RunStateMachine()
        self.dispatcher = ExecutionDispatcher(self.state_machine)
        self.patch_coordinator = (
            PatchCoordinator(coding_port) if coding_port is not None else None
        )
        self.guard = ExperimentSpecificationGuard()

    def execute(self, plan: ReproductionExecutionPlan, run_id: str) -> ReproductionRun:
        run = create_reproduction_run(plan, run_id)
        self.repository.create(run)
        return self._continue(plan, run)

    def resume(self, plan: ReproductionExecutionPlan, run_id: str) -> ReproductionRun:
        """Resume one persisted non-terminal aggregate without creating a second run."""

        run = self.repository.get(run_id)
        if run.status in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}:
            return run
        verify_plan(run, plan)
        return self._continue(plan, run)

    def _continue(self, plan: ReproductionExecutionPlan, run: ReproductionRun) -> ReproductionRun:
        if run.status is RunStatus.PENDING:
            run = self._transition_run(run, RunStatus.QUEUED)
        if self._cancel_requested(run.run_id):
            return self._cancel(run)
        if run.status is RunStatus.QUEUED:
            run = self._transition_run(run, RunStatus.PREPARING)
        if run.status is RunStatus.PREPARING:
            verify_plan(run, plan)
            run = self._transition_run(run, RunStatus.RUNNING)
        if run.status is not RunStatus.RUNNING:
            raise RuntimeError(f"cannot continue reproduction run from {run.status.value}")

        while True:
            if self._cancel_requested(run.run_id):
                return self._cancel(run)
            recovered = self._recover_interrupted_steps(run)
            if recovered != run:
                self._save(run, recovered)
                run = recovered
            reconciled = self.dispatcher.reconcile(run)
            if reconciled != run:
                self._save(run, reconciled)
                run = reconciled
            step = self.dispatcher.next_runnable(run)
            if step is None:
                break
            try:
                run = self._execute_step(run, plan, step.step_id)
            except WorkspaceCleanupError as exc:
                run = self.repository.get(run.run_id)
                failure = self.classifier.record(
                    step.step_id,
                    1,
                    FailureCategory.RESOURCE,
                    "WORKSPACE_CLEANUP_FAILED",
                    "run-private workspace cleanup failed",
                    retryable=False,
                    details={"exception_type": type(exc).__name__},
                )
                return self._transition_run(run, RunStatus.FAILED, failure=failure)

        if all(item.status is StepStatus.SUCCEEDED for item in run.steps):
            return self._transition_run(run, RunStatus.SUCCEEDED)
        failure = next(
            (item.failure for item in run.steps if item.failure is not None),
            self.classifier.record(
                "run",
                1,
                FailureCategory.UNKNOWN,
                "RUN_INCOMPLETE",
                "reproduction run ended without completing every step",
                retryable=False,
            ),
        )
        return self._transition_run(run, RunStatus.FAILED, failure=failure)

    def _execute_step(self, run, plan, step_id):
        step_record=self.state_machine.step(run,step_id)
        experiment = next(item for item in plan.experiments if item.id == step_record.experiment_id)
        action=self._action(experiment,step_id)
        if action is not None and action.action_type is ExperimentActionType.AGGREGATE:
            return self._execute_aggregate_step(run,experiment,action,step_id)
        runtime_run_id = f"{run.run_id}:step:{step_id}"
        context = self.context_factory.create(plan, experiment.id, runtime_run_id,action=action)
        run = self._transition_step(run, step_id, StepStatus.PREPARING)
        try:
            workspace = self.workspace_port.prepare(context)
        except Exception as exc:
            failure = self.classifier.record(
                step_id,
                1,
                FailureCategory.ENVIRONMENT,
                "WORKSPACE_PREPARATION_FAILED",
                "run-private workspace preparation failed",
                retryable=False,
                details={"exception_type": type(exc).__name__},
            )
            attempt = self._exception_attempt(context, 1, failure)
            run = self._append_attempt(run, step_id, attempt)
            return self._transition_step(run, step_id, StepStatus.FAILED, failure=failure)

        try:
            raw_plan = architect_plan(
                context,
                context.constraints.values(ConstraintLevel.LOCKED),
            )
            curie_plan = CuriePlanRecord.model_validate(raw_plan)
            scheduler_partition(curie_plan)
            architect_guard = self.guard.validate_values(context, curie_plan.locked_snapshot)
            if not architect_guard.valid:
                failure = self.classifier.record(
                    step_id,
                    1,
                    FailureCategory.VALIDATION,
                    "ARCHITECT_CHANGED_EXECUTION_TRUTH",
                    "Curie Architect changed locked execution-plan values",
                    retryable=False,
                )
                attempt = self._exception_attempt(context, 1, failure)
                run = self._append_attempt(run, step_id, attempt)
                return self._transition_step(run, step_id, StepStatus.FAILED, failure=failure)

            run = self._transition_step(run, step_id, StepStatus.RUNNING)
            final_result = None
            final_validations = ()
            first_attempt = len(step_record.attempts) + 1
            pending_allocation_adaptation = None
            pending_allocation_patch = None
            if self.resource_adaptation_port is not None:
                pending_allocation_adaptation, pending_allocation_patch = self.resource_adaptation_port.prepare_for_allocation(
                    context,
                    orchestration_run_id=run.run_id,
                    attempts=self.state_machine.step(run, step_id).attempts,
                )
                if (
                    pending_allocation_adaptation is not None
                    and pending_allocation_adaptation.outcome
                    is ResourceAdaptationOutcome.WAITING_FOR_RESOURCES
                ):
                    raise ResourceWaitRequired(
                        pending_allocation_adaptation.updated_requirement,
                        step_id,
                        pending_allocation_adaptation.reason,
                    )
                if (
                    pending_allocation_adaptation is not None
                    and pending_allocation_adaptation.outcome
                    not in {
                        ResourceAdaptationOutcome.RETRY,
                        ResourceAdaptationOutcome.PATCH_AND_RETRY,
                    }
                ):
                    failure = self.classifier.record(
                        step_id, first_attempt, FailureCategory.RESOURCE,
                        "RESOURCE_ADAPTATION_BLOCKED",
                        pending_allocation_adaptation.reason,
                        retryable=False,
                    )
                    attempt = self._exception_attempt(context, first_attempt, failure)
                    attempt = attempt.model_copy(
                        update={
                            "patches": tuple(
                                patch for patch in (pending_allocation_patch,) if patch is not None
                            ),
                            "resource_adaptations": tuple(
                                item.record for item in (pending_allocation_adaptation,)
                                if item is not None and item.record is not None
                            ),
                        }
                    )
                    self.resource_adaptation_port.clear_pending(context)
                    run = self._append_attempt(run, step_id, attempt)
                    return self._transition_step(run, step_id, StepStatus.FAILED, failure=failure)
            for attempt_number in range(first_attempt, self._attempt_limit() + 1):
                if attempt_number > first_attempt:
                    run = self._transition_step(run, step_id, StepStatus.RUNNING)
                started = datetime.now(timezone.utc)
                request = CommandExecutionRequest.model_validate(
                    technician_command(
                        context,
                        workspace,
                        self._timeout(experiment),
                    )
                )
                if self.resource_adaptation_port is not None:
                    current_step = self.state_machine.step(run, step_id)
                    request = self.resource_adaptation_port.apply(
                        context, request, current_step.attempts,
                    )
                try:
                    result = self.command_port.execute(request)
                except Exception as exc:
                    failure = self.classifier.record(
                        step_id,
                        attempt_number,
                        FailureCategory.UNKNOWN,
                        "RUNTIME_PORT_ERROR",
                        "runtime command port failed",
                        retryable=True,
                        details={"exception_type": type(exc).__name__},
                    )
                    attempt = self._exception_attempt(
                        context,
                        attempt_number,
                        failure,
                        started=started,
                    )
                    attempt = attempt.model_copy(
                        update={
                            "patches": tuple(
                                patch for patch in (pending_allocation_patch,)
                                if patch is not None
                            ),
                            "resource_adaptations": tuple(
                                item.record for item in (pending_allocation_adaptation,)
                                if item is not None and item.record is not None
                            ),
                        }
                    )
                    pending_allocation_adaptation = None
                    pending_allocation_patch = None
                    if self.resource_adaptation_port is not None:
                        self.resource_adaptation_port.clear_pending(context)
                    run = self._append_attempt(run, step_id, attempt)
                    if not self.retry_policy.allows(failure, attempt_number):
                        return self._transition_step(run, step_id, StepStatus.FAILED, failure=failure)
                    run = self._transition_step(run, step_id, StepStatus.RETRYING)
                    continue

                if self._cancel_requested(run.run_id):
                    attempt = AttemptRecord(
                        attempt_number=attempt_number,
                        command_id=request.command_id,
                        status=AttemptStatus.CANCELLED,
                        started_at=started,
                        finished_at=datetime.now(timezone.utc),
                        exit_code=result.exit_code,
                    )
                    run = self._append_attempt(run, step_id, attempt)
                    run = self._transition_step(run, step_id, StepStatus.CANCELLED)
                    return run

                run = self._transition_step(run, step_id, StepStatus.VALIDATING)
                collection_failure = None
                try:
                    artifacts = self._collect_artifacts(
                        context,
                        workspace,
                        result,
                        step_id,
                        attempt_number,
                    )
                    deterministic = self.validator.validate(
                        experiment,
                        result,
                        artifacts,
                        step_id=step_id,
                        attempt_number=attempt_number,
                    )
                except Exception as exc:
                    artifacts = ()
                    collection_failure = self.classifier.record(
                        step_id,
                        attempt_number,
                        FailureCategory.UNKNOWN,
                        "ARTIFACT_COLLECTION_FAILED",
                        "artifact collection port failed",
                        retryable=True,
                        details={"exception_type": type(exc).__name__},
                    )
                    deterministic = (
                        self._artifact_collection_validation(
                            step_id,
                            attempt_number,
                            type(exc).__name__,
                        ),
                    )
                canonical_result = None
                needs_result=action.produces_run_result if action is not None else bool(experiment.metadata.get("requires_final_result"))
                resolution_policy=self._run_policy(experiment.evaluation_policy,action)
                if all(item.passed for item in deterministic) and needs_result:
                    canonical_result, final_result_validation = self._resolve_final_result(
                        plan,
                        run,
                        experiment,
                        result,
                        artifacts,
                        step_id,
                        attempt_number,
                        resolution_policy,
                        resource_adaptations=tuple(
                            item.record
                            for item in (pending_allocation_adaptation,)
                            if item is not None and item.record is not None
                        ),
                    )
                    deterministic = (*deterministic, final_result_validation)
                validations = deterministic
                if all(item.passed for item in deterministic) and self.semantic_validation_port:
                    validations = (
                        *validations,
                        self._semantic_validate(
                            run,
                            experiment,
                            result,
                            artifacts,
                            deterministic,
                            step_id,
                            attempt_number,
                        ),
                    )
                failure = collection_failure or self.classifier.classify_execution(
                    result,
                    step_id=step_id,
                    attempt_number=attempt_number,
                )
                if failure is None and not all(item.passed for item in validations):
                    failure = self.classifier.validation_failure(
                        step_id=step_id,
                        attempt_number=attempt_number,
                        violations=tuple(
                            violation
                            for item in validations
                            for violation in item.violations
                        ),
                    )
                adaptation_decision = None
                resource_patch = None
                if (
                    failure is not None
                    and failure.code == "GPU_OOM"
                    and self.resource_adaptation_port is not None
                ):
                    current_step = self.state_machine.step(run, step_id)
                    adaptation_decision, resource_patch = self.resource_adaptation_port.handle_oom(
                        context,
                        orchestration_run_id=run.run_id,
                        attempt_number=attempt_number,
                        attempts=current_step.attempts,
                    )
                    if adaptation_decision.outcome in {
                        ResourceAdaptationOutcome.RESOURCE_UNSATISFIABLE,
                        ResourceAdaptationOutcome.BLOCKED,
                    }:
                        code = (
                            "RESOURCE_UNSATISFIABLE"
                            if adaptation_decision.outcome
                            is ResourceAdaptationOutcome.RESOURCE_UNSATISFIABLE
                            else "RESOURCE_ADAPTATION_BLOCKED"
                        )
                        failure = self.classifier.record(
                            step_id,
                            attempt_number,
                            FailureCategory.RESOURCE,
                            code,
                            adaptation_decision.reason,
                            retryable=False,
                            details={"original_failure_id": failure.failure_id},
                        )
                attempt = AttemptRecord(
                    attempt_number=attempt_number,
                    command_id=request.command_id,
                    status=self._attempt_status(result, failure),
                    started_at=started,
                    finished_at=datetime.now(timezone.utc),
                    exit_code=result.exit_code,
                    stdout_reference=result.stdout_reference,
                    stderr_reference=result.stderr_reference,
                    failures=(failure,) if failure else (),
                    validations=validations,
                    artifacts=artifacts,
                    metrics=result.metrics,
                    final_result=canonical_result,
                    patches=tuple(
                        patch for patch in (pending_allocation_patch, resource_patch)
                        if patch is not None
                    ),
                    resource_adaptations=tuple(
                        item.record
                        for item in (pending_allocation_adaptation, adaptation_decision)
                        if item is not None and item.record is not None
                    ),
                )
                pending_allocation_adaptation = None
                pending_allocation_patch = None
                if self.resource_adaptation_port is not None:
                    self.resource_adaptation_port.clear_pending(context)
                run = self._append_attempt(run, step_id, attempt)
                final_result = result
                final_validations = validations
                if failure is None:
                    analysis, conclusion = self._analyze(result, validations)
                    return self._transition_step(
                        run,
                        step_id,
                        StepStatus.SUCCEEDED,
                        artifacts=artifacts,
                        analysis=analysis,
                        conclusion=conclusion,
                        final_result=canonical_result,
                    )
                if adaptation_decision is not None:
                    if adaptation_decision.outcome is ResourceAdaptationOutcome.WAITING_FOR_RESOURCES:
                        run = self._transition_step(run, step_id, StepStatus.RETRYING)
                        raise ResourceWaitRequired(
                            adaptation_decision.updated_requirement,
                            step_id,
                            adaptation_decision.reason,
                        )
                    if adaptation_decision.outcome in {
                        ResourceAdaptationOutcome.RETRY,
                        ResourceAdaptationOutcome.PATCH_AND_RETRY,
                    }:
                        run = self._transition_step(run, step_id, StepStatus.RETRYING)
                        continue
                if not self.retry_policy.allows(failure, attempt_number):
                    break
                if self.retry_policy.requires_patch(failure):
                    if self.patch_coordinator is None:
                        break
                    run = self._transition_step(run, step_id, StepStatus.PATCHING)
                    patch = self.patch_coordinator.apply(context, failure, attempt_number)
                    run = self._attach_patch(run, step_id, patch)
                    if patch.status is not PatchStatus.APPLIED:
                        break
                run = self._transition_step(run, step_id, StepStatus.RETRYING)

            step = self.state_machine.step(run, step_id)
            failure = step.attempts[-1].failures[-1]
            analysis, conclusion = self._analyze(final_result, final_validations)
            return self._transition_step(
                run,
                step_id,
                StepStatus.FAILED,
                failure=failure,
                analysis=analysis,
                conclusion=conclusion,
            )
        finally:
            try:
                self.workspace_port.cleanup(runtime_run_id)
            except Exception as exc:
                raise WorkspaceCleanupError(
                    f"run-private workspace cleanup failed for {runtime_run_id!r}"
                ) from exc

    def _semantic_validate(
        self,
        run,
        experiment,
        result,
        artifacts,
        deterministic,
        step_id,
        attempt_number,
    ):
        try:
            value = self.semantic_validation_port.validate(
                SemanticValidationRequest(
                    run_id=run.run_id,
                    step_id=step_id,
                    attempt_number=attempt_number,
                    experiment=experiment,
                    execution_result=result,
                    artifacts=artifacts,
                    deterministic_validations=deterministic,
                )
            )
            passed, status, violations, details = (
                value.passed,
                value.status,
                value.violations,
                value.details,
            )
        except Exception as exc:
            passed, status, violations, details = (
                False,
                "error",
                ("semantic validator failed",),
                {"exception_type": type(exc).__name__},
            )
        digest = hashlib.sha256(
            f"{step_id}:{attempt_number}:semantic".encode()
        ).hexdigest()[:20]
        return ValidationRecord(
            validation_id=f"validation:{digest}",
            validator_name="semantic_validator",
            phase=ValidationPhase.SEMANTIC,
            passed=passed,
            status=status,
            violations=violations,
            details=details,
        )

    def _collect_artifacts(self, context, workspace, result, step_id, attempt_number):
        combined = (*result.artifacts, *self.artifact_port.collect(context, workspace))
        unique = {(item.name, item.uri): item for item in combined}
        return tuple(
            ArtifactReference(
                step_id=step_id,
                attempt_number=attempt_number,
                artifact=item,
            )
            for item in unique.values()
        )

    def _resolve_final_result(
        self, plan, run, experiment, result, artifacts, step_id,
        attempt_number, policy, *, resource_adaptations=(),
    ):
        failure = None
        value = None
        if self.result_resolver is None:
            failure = "no ResultResolver is configured"
        elif policy is None or not policy.is_resolved:
            failure = "execution plan lacks a resolved EvaluationPolicy"
        else:
            try:
                step=self.state_machine.step(run,step_id)
                inherited=tuple(
                    reference.artifact
                    for parent_id in step.depends_on_step_ids
                    for reference in self.state_machine.step(run,parent_id).artifacts
                )
                available_artifacts=tuple({(item.name,item.uri):item for item in (*inherited,*(reference.artifact for reference in artifacts))}.values())
                value = self.result_resolver.resolve(
                    ResultResolutionRequest(
                        repository_id=plan.repository.repository_id,
                        repository_snapshot_id=plan.repository_snapshot_id,
                        paper_experiment_id=str(experiment.metadata.get("paper_experiment_id")),
                        orchestration_run_id=run.run_id,
                        evaluation_policy=policy,
                        observed_metrics=result.metrics,
                        artifacts=available_artifacts,
                        stdout_reference=result.stdout_reference,
                        stderr_reference=result.stderr_reference,
                        provenance={
                            "step_id":step_id,
                            "attempt_number":str(attempt_number),
                            "resource_adaptations":[
                                record.model_dump(mode="json")
                                for record in (
                                    *(record for attempt in step.attempts for record in attempt.resource_adaptations),
                                    *resource_adaptations,
                                )
                            ],
                        },
                    )
                )
                if value.paper_experiment_id != experiment.metadata.get("paper_experiment_id"):
                    raise ValueError("ResultResolver changed the selected paper experiment")
                if value.evaluation_policy != policy:
                    raise ValueError("ResultResolver changed the locked EvaluationPolicy")
            except Exception as exc:
                value = None
                failure = f"FinalResult resolution failed: {type(exc).__name__}"
        digest = hashlib.sha256(
            f"{step_id}:{attempt_number}:deterministic:final_result".encode()
        ).hexdigest()[:20]
        return value, ValidationRecord(
            validation_id=f"validation:{digest}",
            validator_name="canonical_final_result",
            phase=ValidationPhase.DETERMINISTIC,
            passed=value is not None,
            status="passed" if value is not None else "missing",
            violations=() if value is not None else (failure or "FinalResult is missing",),
            details={} if value is None else {"final_result_id":value.result_id},
        )

    def _execute_aggregate_step(self,run,experiment,action,step_id):
        run=self._transition_step(run,step_id,StepStatus.PREPARING)
        run=self._transition_step(run,step_id,StepStatus.RUNNING)
        run=self._transition_step(run,step_id,StepStatus.VALIDATING)
        started=datetime.now(timezone.utc);value=None;violation=None
        try:
            parents=[self.state_machine.step(run,parent) for parent in action.depends_on_action_ids]
            run_results=tuple(
                parent.final_result.runs[0]
                for parent in parents
                if parent.final_result is not None and len(parent.final_result.runs)==1
            )
            if experiment.evaluation_policy is None:raise ValueError("aggregate action lacks EvaluationPolicy")
            value=aggregate_final_result(
                action.paper_experiment_id,
                experiment.evaluation_policy,
                run_results,
                provenance={
                    "orchestration_run_id":run.run_id,
                    "step_id":step_id,
                    "resource_adaptations":[
                        record
                        for run_result in run_results
                        for records in (run_result.provenance.get("resource_adaptations", []),)
                        if isinstance(records, list)
                        for record in records
                    ],
                },
            )
        except Exception as exc:
            violation=f"FinalResult aggregation failed: {type(exc).__name__}"
        digest=hashlib.sha256(f"{step_id}:1:deterministic:aggregate".encode()).hexdigest()[:20]
        validation=ValidationRecord(validation_id=f"validation:{digest}",validator_name="canonical_aggregation",phase=ValidationPhase.DETERMINISTIC,passed=value is not None,status="passed" if value is not None else "invalid",violations=() if value is not None else (violation or "aggregation failed",),details={} if value is None else {"final_result_id":value.result_id})
        failure=None if value is not None else self.classifier.record(step_id,1,FailureCategory.VALIDATION,"FINAL_RESULT_AGGREGATION_FAILED","per-run final results could not be canonically aggregated",retryable=False,details={"violation":violation})
        attempt=AttemptRecord(attempt_number=1,command_id=f"internal:aggregate:{step_id}",status=AttemptStatus.SUCCEEDED if value is not None else AttemptStatus.FAILED,started_at=started,finished_at=datetime.now(timezone.utc),exit_code=0 if value is not None else 1,failures=() if failure is None else (failure,),validations=(validation,),final_result=value)
        run=self._append_attempt(run,step_id,attempt)
        if failure is not None:return self._transition_step(run,step_id,StepStatus.FAILED,failure=failure)
        return self._transition_step(run,step_id,StepStatus.SUCCEEDED,final_result=value,analysis={"aggregation":experiment.evaluation_policy.aggregation.value,"runs":len(value.runs)},conclusion="Canonical per-run results were aggregated without best-seed selection.")

    @staticmethod
    def _action(experiment,step_id):
        if experiment.action_plan is None:return None
        return next(item for item in experiment.action_plan.actions if item.action_id==step_id)

    @staticmethod
    def _run_policy(policy,action):
        if policy is None or action is None or policy.run_count==1:return policy
        seeds=(action.seed,) if action.seed is not None else ()
        return policy.model_copy(update={"run_count":1,"seeds":seeds,"aggregation":ResultAggregation.NONE})

    @staticmethod
    def _artifact_collection_validation(step_id, attempt_number, exception_type):
        digest = hashlib.sha256(
            f"{step_id}:{attempt_number}:artifact_collection".encode()
        ).hexdigest()[:20]
        return ValidationRecord(
            validation_id=f"validation:{digest}",
            validator_name="artifact_collection",
            phase=ValidationPhase.DETERMINISTIC,
            passed=False,
            status="error",
            violations=("artifact collection failed",),
            details={"exception_type": exception_type},
        )

    @staticmethod
    def _attempt_status(result, failure):
        if failure is None:
            return AttemptStatus.SUCCEEDED
        if result.status is ExecutionStatus.TIMED_OUT:
            return AttemptStatus.TIMED_OUT
        return AttemptStatus.FAILED

    @staticmethod
    def _analyze(result, validations):
        if result is None:
            return None, "Execution failed before a result was available."
        analysis = analyzer_interpret(result)
        curie_validations = tuple(
            CurieValidationRecord(
                validator_name=item.validator_name,
                valid=item.passed,
                status=item.status,
                violations=item.violations,
            )
            for item in validations
        )
        return analysis, concluder_decide(result, curie_validations)

    def _exception_attempt(self, context, number, failure, *, started=None):
        started = started or datetime.now(timezone.utc)
        return AttemptRecord(
            attempt_number=number,
            command_id=context.command.command_reference_id or f"command:{context.experiment_id}",
            status=AttemptStatus.FAILED,
            started_at=started,
            finished_at=datetime.now(timezone.utc),
            failures=(failure,),
        )

    def _append_attempt(self, run, step_id, attempt):
        step = self.state_machine.step(run, step_id)
        replacement = self._copy(step, attempts=(*step.attempts, attempt))
        updated = self.state_machine.replace_step(run, replacement)
        self._save(run, updated)
        return updated

    def _recover_interrupted_steps(self, run):
        transient = {
            StepStatus.PREPARING,
            StepStatus.RUNNING,
            StepStatus.VALIDATING,
            StepStatus.PATCHING,
            StepStatus.RETRYING,
        }
        interrupted = tuple(step for step in run.steps if step.status in transient)
        if len(interrupted) > 1:
            raise RuntimeError("persisted run has multiple active steps and cannot be recovered safely")
        current = run
        for original in interrupted:
            step = self.state_machine.step(current, original.step_id)
            if step.status not in transient:
                continue
            last = step.attempts[-1] if step.attempts else None
            if last is not None and last.status is AttemptStatus.SUCCEEDED:
                replacement = self._copy(
                    step,
                    status=StepStatus.SUCCEEDED,
                    artifacts=last.artifacts,
                    final_result=last.final_result,
                    failure=None,
                    analysis=step.analysis or {"recovery": "persisted_successful_attempt"},
                    conclusion=step.conclusion or "Recovered a persisted successful attempt.",
                    finished_at=datetime.now(timezone.utc),
                )
            elif last is not None and last.status is AttemptStatus.CANCELLED:
                replacement = self._copy(
                    step,
                    status=StepStatus.CANCELLED,
                    finished_at=datetime.now(timezone.utc),
                )
            elif len(step.attempts) >= self._attempt_limit():
                failure = (
                    last.failures[-1]
                    if last is not None and last.failures
                    else self.classifier.record(
                        step.step_id,
                        max(1, len(step.attempts)),
                        FailureCategory.UNKNOWN,
                        "INTERRUPTED_ATTEMPTS_EXHAUSTED",
                        "worker stopped after exhausting persisted attempts",
                        retryable=False,
                    )
                )
                replacement = self._copy(
                    step,
                    status=StepStatus.FAILED,
                    failure=failure,
                    finished_at=datetime.now(timezone.utc),
                )
            else:
                replacement = self._copy(
                    step,
                    status=StepStatus.READY,
                    failure=None,
                    finished_at=None,
                )
            current = self.state_machine.replace_step(current, replacement)
        return current

    def _attempt_limit(self):
        resource_limit = (
            self.resource_adaptation_port.max_attempts
            if self.resource_adaptation_port is not None
            else 0
        )
        return max(self.retry_policy.max_attempts, resource_limit)

    def _attach_patch(self, run, step_id, patch):
        step = self.state_machine.step(run, step_id)
        attempt = step.attempts[-1]
        replacement_attempt = self._copy(attempt, patches=(*attempt.patches, patch))
        replacement_step = self._copy(
            step,
            attempts=(*step.attempts[:-1], replacement_attempt),
        )
        updated = self.state_machine.replace_step(run, replacement_step)
        self._save(run, updated)
        return updated

    def _transition_run(self, run, target, **changes):
        updated = self.state_machine.transition_run(run, target, **changes)
        self._save(run, updated)
        return updated

    def _transition_step(self, run, step_id, target, **changes):
        updated = self.state_machine.transition_step(run, step_id, target, **changes)
        self._save(run, updated)
        return updated

    def _save(self, previous, updated):
        self.repository.save(updated, expected_revision=previous.revision)

    def _cancel_requested(self, run_id):
        return bool(
            self.cancellation_port
            and self.cancellation_port.is_cancel_requested(run_id)
        )

    def _cancel(self, run):
        current = run
        terminal = {
            StepStatus.SUCCEEDED,
            StepStatus.FAILED,
            StepStatus.BLOCKED,
            StepStatus.CANCELLED,
        }
        for step in tuple(current.steps):
            latest = self.state_machine.step(current, step.step_id)
            if latest.status not in terminal:
                current = self._transition_step(
                    current,
                    latest.step_id,
                    StepStatus.CANCELLED,
                )
        return self._transition_run(current, RunStatus.CANCELLED)

    @staticmethod
    def _timeout(experiment):
        value = experiment.metadata.get("timeout_seconds", 3600)
        return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 3600

    @staticmethod
    def _copy(model, **changes):
        values = model.model_dump(mode="python")
        values.update(changes)
        return type(model).model_validate(values)
