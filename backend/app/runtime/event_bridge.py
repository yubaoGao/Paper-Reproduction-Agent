"""Translate Curie workflow activity into typed platform run events."""

from backend.app.domain import (
    AgentEventPayload,
    Artifact,
    CommandEventPayload,
    EventType,
    LogPayload,
    Metric,
    PatchEventPayload,
    PlanEventPayload,
    RunEvent,
    RunStartedPayload,
    RunStatus,
    RunTerminalPayload,
    StatusChangedPayload,
    ValidationEventPayload,
)

from .curie_models import ComponentType


class CurieEventBridge:
    def __init__(self, run_id: str, sink) -> None:
        self.run_id = run_id
        self.sink = sink

    def emit(self, event_type: EventType, payload) -> None:
        self.sink.publish(
            RunEvent(run_id=self.run_id, event_type=event_type, payload=payload)
        )

    def run_started(self) -> None:
        self.emit(EventType.RUN_STARTED, RunStartedPayload(runtime="curie"))
        self.emit(
            EventType.RUN_STATUS_CHANGED,
            StatusChangedPayload(
                previous_status=RunStatus.PENDING,
                status=RunStatus.RUNNING,
                message="Curie reproduction execution started",
            ),
        )

    def component_started(
        self,
        name: str,
        experiment_id: str,
        component_type: ComponentType = ComponentType.AGENT,
    ) -> None:
        self.emit(
            EventType.AGENT_STARTED,
            AgentEventPayload(
                agent_name=name,
                experiment_id=experiment_id,
                component_type=component_type.value,
            ),
        )

    def component_finished(
        self,
        name: str,
        experiment_id: str,
        message: str | None = None,
        component_type: ComponentType = ComponentType.AGENT,
    ) -> None:
        self.emit(
            EventType.AGENT_FINISHED,
            AgentEventPayload(
                agent_name=name,
                experiment_id=experiment_id,
                message=message,
                component_type=component_type.value,
            ),
        )

    def plan_created(self, experiment_id: str, plan) -> None:
        self.emit(
            EventType.PLAN_CREATED,
            PlanEventPayload(
                experiment_id=experiment_id,
                plan_id=plan.plan_id,
                summary=plan.summary,
            ),
        )

    def command_started(self, experiment_id: str, request) -> None:
        self.emit(
            EventType.COMMAND_STARTED,
            CommandEventPayload(
                experiment_id=experiment_id,
                command_id=request.command_id,
                program=request.program,
                status="running",
            ),
        )

    def command_finished(self, experiment_id: str, request, result) -> None:
        self.emit(
            EventType.COMMAND_FINISHED,
            CommandEventPayload(
                experiment_id=experiment_id,
                command_id=request.command_id,
                program=request.program,
                status=result.status.value,
                exit_code=result.exit_code,
                duration_seconds=result.duration_seconds,
            ),
        )

    def validation(self, experiment_id: str, record) -> None:
        self.emit(
            EventType.VALIDATION_RESULT,
            ValidationEventPayload(
                experiment_id=experiment_id,
                validator_name=record.validator_name,
                valid=record.valid,
                status=record.status,
                violations=record.violations,
            ),
        )

    def patch(self, experiment_id: str, record) -> None:
        self.emit(
            EventType.PATCH_CREATED,
            PatchEventPayload(
                experiment_id=experiment_id,
                patch_id=record.patch_id,
                summary=record.summary,
                accepted=record.accepted,
            ),
        )

    def metric(self, value: Metric) -> None:
        self.emit(EventType.METRIC, value)

    def artifact(self, value: Artifact) -> None:
        self.emit(EventType.ARTIFACT_CREATED, value)

    def log(self, message: str, level: str = "INFO") -> None:
        self.emit(EventType.LOG, LogPayload(level=level, message=message))

    def terminal(
        self,
        status: RunStatus,
        error=None,
        exit_code: int | None = None,
    ) -> None:
        self.emit(
            EventType.RUN_STATUS_CHANGED,
            StatusChangedPayload(
                previous_status=RunStatus.RUNNING,
                status=status,
                message=(error.message if error is not None else "Curie run finished"),
            ),
        )
        event_type = (
            EventType.RUN_FAILED
            if status is RunStatus.FAILED
            else EventType.RUN_FINISHED
        )
        self.emit(
            event_type,
            RunTerminalPayload(status=status, error=error, exit_code=exit_code),
        )
