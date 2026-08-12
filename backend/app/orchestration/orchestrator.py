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
    PatchStatus,
    ReproductionExecutionPlan,
    ReproductionRun,
    RunStatus,
    StepStatus,
    ValidationPhase,
    ValidationRecord,
)
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
        self.state_machine = RunStateMachine()
        self.dispatcher = ExecutionDispatcher(self.state_machine)
        self.patch_coordinator = (
            PatchCoordinator(coding_port) if coding_port is not None else None
        )
        self.guard = ExperimentSpecificationGuard()

    def execute(self, plan: ReproductionExecutionPlan, run_id: str) -> ReproductionRun:
        run = create_reproduction_run(plan, run_id)
        self.repository.create(run)
        run = self._transition_run(run, RunStatus.QUEUED)
        if self._cancel_requested(run.run_id):
            return self._cancel(run)
        run = self._transition_run(run, RunStatus.PREPARING)
        verify_plan(run, plan)
        run = self._transition_run(run, RunStatus.RUNNING)

        while True:
            if self._cancel_requested(run.run_id):
                return self._cancel(run)
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
        experiment = next(item for item in plan.experiments if item.id == step_id)
        runtime_run_id = f"{run.run_id}:step:{step_id}"
        context = self.context_factory.create(plan, step_id, runtime_run_id)
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
            for attempt_number in range(1, self.retry_policy.max_attempts + 1):
                if attempt_number > 1:
                    run = self._transition_step(run, step_id, StepStatus.RUNNING)
                started = datetime.now(timezone.utc)
                request = CommandExecutionRequest.model_validate(
                    technician_command(
                        context,
                        workspace,
                        self._timeout(experiment),
                    )
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
                )
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
                    )
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
