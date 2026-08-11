"""Port-driven reproduction mode around retained Curie component logic."""

from __future__ import annotations

from datetime import datetime, timezone

from backend.app.domain import RunError, RunStatus

from .curie_models import (
    CodingRequest,
    CommandExecutionRequest,
    ComponentType,
    ConstraintLevel,
    CurieAgentTraceRecord,
    CurieExecutionResult,
    CuriePatchRecord,
    CuriePlanRecord,
    CurieValidationRecord,
    ExecutionStatus,
)
from .guard import ExperimentSpecificationGuard


_PATCH_CATEGORIES = (
    "import",
    "path",
    "api_mismatch",
    "runtime_error",
    "generated_script",
)


class CurieReproductionWorkflow:
    """Execute one planner-authoritative specification through Curie's stages."""

    def __init__(
        self,
        command_port,
        workspace_port,
        artifact_port,
        *,
        coding_port=None,
        state_store=None,
        max_attempts: int = 2,
        guard=None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        self.command_port = command_port
        self.workspace_port = workspace_port
        self.artifact_port = artifact_port
        self.coding_port = coding_port
        self.state_store = state_store
        self.max_attempts = max_attempts
        self.guard = guard or ExperimentSpecificationGuard()

    def execute(self, context, bridge, timeout_seconds):
        from backend.app.curie_core.reproduction import (
            analyzer_interpret,
            architect_plan,
            concluder_decide,
            exec_validate,
            llm_validator_guard,
            patcher_guard,
            scheduler_partition,
            technician_command,
        )

        started = datetime.now(timezone.utc)
        traces = []
        validations = []
        patches = []
        warnings = []

        def stage(name, function, component_type=ComponentType.AGENT):
            bridge.component_started(name, context.experiment_id, component_type)
            traces.append(
                CurieAgentTraceRecord(
                    component_name=name,
                    component_type=component_type,
                    status="started",
                )
            )
            try:
                value = function()
            except Exception as exc:
                bridge.component_finished(
                    name,
                    context.experiment_id,
                    message=str(exc),
                    component_type=component_type,
                )
                traces.append(
                    CurieAgentTraceRecord(
                        component_name=name,
                        component_type=component_type,
                        status="failed",
                        message=str(exc),
                    )
                )
                raise
            bridge.component_finished(
                name,
                context.experiment_id,
                component_type=component_type,
            )
            traces.append(
                CurieAgentTraceRecord(
                    component_name=name,
                    component_type=component_type,
                    status="finished",
                )
            )
            return value

        workspace = self.workspace_port.prepare(context)
        if self.state_store:
            self.state_store.put(
                tuple(context.namespace.split("/")),
                "workspace",
                workspace.model_dump(mode="json"),
            )

        raw_plan = stage(
            "Architect",
            lambda: architect_plan(
                context,
                context.constraints.values(ConstraintLevel.LOCKED),
            ),
        )
        plan = CuriePlanRecord.model_validate(raw_plan)
        bridge.plan_created(context.experiment_id, plan)
        stage(
            "InternalExperimentScheduler",
            lambda: scheduler_partition(plan),
            ComponentType.SERVICE,
        )

        guarded = stage(
            "LLMValidator",
            lambda: llm_validator_guard(
                self.guard,
                context,
                plan.locked_snapshot,
            ),
        )
        spec_validation = CurieValidationRecord(
            validator_name="LLMValidator+SpecificationGuard",
            valid=guarded.valid,
            status="passed" if guarded.valid else "violation",
            violations=tuple(item.message for item in guarded.violations),
        )
        validations.append(spec_validation)
        bridge.validation(context.experiment_id, spec_validation)
        if not guarded.valid:
            return self._failed(
                context,
                started,
                plan,
                validations,
                patches,
                traces,
                "SPECIFICATION_VIOLATION",
                "Architect plan changed locked constraints",
                warnings,
            )

        preparation_values = context.constraints.values(ConstraintLevel.LOCKED)
        prepared_guard = stage(
            "ExperimentSpecificationGuard",
            lambda: self.guard.validate_values(context, preparation_values),
            ComponentType.SERVICE,
        )
        preparation_validation = CurieValidationRecord(
            validator_name="ExperimentSpecificationGuard",
            valid=prepared_guard.valid,
            status="passed" if prepared_guard.valid else "violation",
            violations=tuple(item.message for item in prepared_guard.violations),
        )
        validations.append(preparation_validation)
        bridge.validation(context.experiment_id, preparation_validation)
        if not prepared_guard.valid:
            return self._failed(
                context,
                started,
                plan,
                validations,
                patches,
                traces,
                "SPECIFICATION_VIOLATION",
                "Execution preparation changed locked constraints",
                warnings,
            )

        request = CommandExecutionRequest.model_validate(
            technician_command(context, workspace, timeout_seconds)
        )
        last = None
        attempts = 0
        while attempts < self.max_attempts:
            attempts += 1

            def execute_command():
                bridge.command_started(context.experiment_id, request)
                result = self.command_port.execute(request)
                bridge.command_finished(context.experiment_id, request, result)
                return result

            last = stage("Technician", execute_command)
            exec_raw = stage(
                "ExecValidator",
                lambda: exec_validate(last),
                ComponentType.SERVICE,
            )
            exec_record = CurieValidationRecord(
                validator_name="ExecValidator",
                **exec_raw,
            )
            validations.append(exec_record)
            bridge.validation(context.experiment_id, exec_record)
            if exec_record.valid:
                break
            if (
                last.status is ExecutionStatus.TIMED_OUT
                or self.coding_port is None
                or attempts >= self.max_attempts
            ):
                break

            coding_request = CodingRequest(
                run_id=context.run_id,
                experiment_id=context.experiment_id,
                instruction=(
                    "Repair the local runtime failure without changing locked "
                    "scientific constraints."
                ),
                allowed_change_categories=_PATCH_CATEGORIES,
                locked_constraint_keys=tuple(
                    context.constraints.values(ConstraintLevel.LOCKED)
                ),
            )
            coding = stage(
                "Patcher",
                lambda: self.coding_port.apply(coding_request),
            )
            patch_guard = patcher_guard(
                self.guard,
                context,
                coding.proposed_values,
            )
            disallowed = tuple(
                category
                for category in coding.changed_categories
                if category not in _PATCH_CATEGORIES
            )
            patch = CuriePatchRecord(
                patch_id=coding.patch_id,
                summary=coding.summary,
                accepted=patch_guard.valid and not disallowed,
                violations=(
                    *(item.message for item in patch_guard.violations),
                    *(f"patch category {item!r} is not allowed" for item in disallowed),
                ),
            )
            patches.append(patch)
            bridge.patch(context.experiment_id, patch)
            if not patch.accepted:
                break

        if last is None:
            raise RuntimeError("Curie workflow completed without an execution attempt")

        analysis = stage("Analyzer", lambda: analyzer_interpret(last))
        conclusion = stage(
            "Concluder",
            lambda: concluder_decide(last, validations),
        )
        artifacts = list(last.artifacts)
        artifacts.extend(self.artifact_port.collect(context, workspace))
        unique_artifacts = tuple(
            {(item.name, item.uri): item for item in artifacts}.values()
        )
        metrics = last.metrics
        for metric in metrics:
            bridge.metric(metric)
        for artifact in unique_artifacts:
            bridge.artifact(artifact)

        finished = datetime.now(timezone.utc)
        latest_validations = {
            item.validator_name: item for item in validations
        }
        if (
            last.status is ExecutionStatus.SUCCEEDED
            and all(item.valid for item in latest_validations.values())
        ):
            return CurieExecutionResult(
                run_id=context.run_id,
                experiment_id=context.experiment_id,
                status=RunStatus.SUCCEEDED,
                plans=(plan,),
                attempts=attempts,
                validation_results=tuple(validations),
                patches=tuple(patches),
                metrics=metrics,
                artifacts=unique_artifacts,
                analysis=analysis,
                conclusion=conclusion,
                warnings=tuple(warnings),
                agent_trace=tuple(traces),
                exit_code=0,
                started_at=started,
                finished_at=finished,
            )

        code = (
            "EXECUTION_TIMEOUT"
            if last.status is ExecutionStatus.TIMED_OUT
            else "EXECUTION_FAILED"
        )
        message = (
            "experiment command timed out"
            if last.status is ExecutionStatus.TIMED_OUT
            else "experiment execution or validation failed"
        )
        return CurieExecutionResult(
            run_id=context.run_id,
            experiment_id=context.experiment_id,
            status=RunStatus.FAILED,
            plans=(plan,),
            attempts=attempts,
            validation_results=tuple(validations),
            patches=tuple(patches),
            metrics=metrics,
            artifacts=unique_artifacts,
            analysis=analysis,
            conclusion=conclusion,
            warnings=tuple(warnings),
            agent_trace=tuple(traces),
            error=RunError(
                code=code,
                message=message,
                retryable=last.status is not ExecutionStatus.TIMED_OUT,
            ),
            exit_code=last.exit_code,
            started_at=started,
            finished_at=finished,
        )

    @staticmethod
    def _failed(
        context,
        started,
        plan,
        validations,
        patches,
        traces,
        code,
        message,
        warnings,
    ):
        return CurieExecutionResult(
            run_id=context.run_id,
            experiment_id=context.experiment_id,
            status=RunStatus.FAILED,
            plans=(plan,),
            validation_results=tuple(validations),
            patches=tuple(patches),
            agent_trace=tuple(traces),
            error=RunError(code=code, message=message),
            started_at=started,
            finished_at=datetime.now(timezone.utc),
            warnings=tuple(warnings),
        )

