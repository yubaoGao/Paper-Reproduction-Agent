"""Stable experiment domain models shared across PaperReproAgent layers."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    SerializeAsAny,
    StringConstraints,
    field_validator,
    model_validator,
)


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def utc_now() -> datetime:
    """Return an aware UTC timestamp for domain defaults."""

    return datetime.now(timezone.utc)


def _require_aware(value: datetime | None, field_name: str) -> datetime | None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError(f"{field_name} must include timezone information")
    return value


class DomainModel(BaseModel):
    """Common strict configuration for value-oriented domain models."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class ExperimentTaskType(str, Enum):
    """Supported high-level reproduction intents."""

    FULL_REPRODUCTION = "full_reproduction"
    ABLATION = "ablation"
    BASELINE_REPRODUCTION = "baseline_reproduction"
    CUSTOM = "custom"


class RunStatus(str, Enum):
    """Lifecycle states owned by platform run orchestration."""

    PENDING = "pending"
    QUEUED = "queued"
    PREPARING = "preparing"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ArtifactKind(str, Enum):
    CHECKPOINT = "checkpoint"
    LOG = "log"
    CONFIG = "config"
    RESULT = "result"
    PLOT = "plot"
    REPORT = "report"
    OTHER = "other"


class EventType(str, Enum):
    RUN_STARTED = "run_started"
    RUN_STATUS_CHANGED = "run_status_changed"
    AGENT_STARTED = "agent_started"
    AGENT_FINISHED = "agent_finished"
    PLAN_CREATED = "plan_created"
    PLAN_UPDATED = "plan_updated"
    COMMAND_STARTED = "command_started"
    COMMAND_FINISHED = "command_finished"
    PATCH_CREATED = "patch_created"
    VALIDATION_RESULT = "validation_result"
    LOG = "log"
    METRIC = "metric"
    ARTIFACT_CREATED = "artifact_created"
    RUN_FINISHED = "run_finished"
    RUN_FAILED = "run_failed"


class RepositorySource(DomainModel):
    """Immutable reference to experiment source code, not a checked-out repository."""

    uri: NonEmptyStr
    revision: NonEmptyStr | None = None
    subdirectory: NonEmptyStr | None = None


class DatasetSource(DomainModel):
    """Reference to a dataset version; resolving or downloading it is adapter work."""

    uri: NonEmptyStr
    name: NonEmptyStr | None = None
    revision: NonEmptyStr | None = None


class EnvironmentSpecification(DomainModel):
    """Portable environment declaration without provisioning behavior."""

    python_version: NonEmptyStr | None = None
    dependencies: tuple[NonEmptyStr, ...] = ()
    variables: dict[NonEmptyStr, str] = Field(default_factory=dict)


class MetricExpectation(DomainModel):
    """A paper-reported or otherwise expected metric used for comparison."""

    name: NonEmptyStr
    value: float = Field(allow_inf_nan=False)
    split: NonEmptyStr | None = None
    unit: NonEmptyStr | None = None
    tolerance: float | None = Field(default=None, ge=0, allow_inf_nan=False)

    @field_validator("value", "tolerance", mode="before")
    @classmethod
    def reject_boolean_numbers(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("boolean values are not valid metrics")
        return value

class DatasetAvailability(str,Enum): AVAILABLE="available"; BINDING_REQUIRED="binding_required"; UNKNOWN="unknown"
class ExecutableCommand(DomainModel):
    """Argument-vector command declaration; never a shell expression."""
    program:NonEmptyStr;arguments:tuple[NonEmptyStr,...]=();working_directory:NonEmptyStr=".";environment_variable_references:tuple[NonEmptyStr,...]=();entrypoint_id:NonEmptyStr|None=None;config_ids:tuple[NonEmptyStr,...]=();command_reference_id:NonEmptyStr|None=None
    @model_validator(mode="after")
    def reject_shell_syntax(self):
        unsafe={"&&","||",";","|",">","<"}
        if self.program in unsafe or any(x in unsafe or "\n" in x or "\r" in x for x in self.arguments):raise ValueError("structured command cannot contain shell control operators")
        return self
class DatasetRequirement(DomainModel):
    name:NonEmptyStr;repository_dataset_id:NonEmptyStr|None=None;binding:NonEmptyStr|None=None;split:NonEmptyStr|None=None;preprocessing_assumptions:tuple[NonEmptyStr,...]=();loader_references:tuple[NonEmptyStr,...]=();paper_evidence:tuple[SerializeAsAny[DomainModel],...]=();repository_evidence:tuple[SerializeAsAny[DomainModel],...]=();availability:DatasetAvailability
    @model_validator(mode="after")
    def binding_matches_status(self):
        if self.availability is DatasetAvailability.AVAILABLE and self.binding is None:raise ValueError("available dataset requires a binding")
        return self
class EnvironmentRequirement(DomainModel):
    python_constraint:NonEmptyStr|None=None;dependencies:tuple[NonEmptyStr,...]=();system_dependencies:tuple[NonEmptyStr,...]=();frameworks:tuple[NonEmptyStr,...]=();cuda_hints:tuple[NonEmptyStr,...]=();manifest_references:tuple[NonEmptyStr,...]=()
class ResourceRequirement(DomainModel):
    gpu_required:bool|None=None;gpu_count:int|None=Field(default=None,ge=0);cpu_cores:float|None=Field(default=None,gt=0);memory_mb:int|None=Field(default=None,ge=128);notes:tuple[NonEmptyStr,...]=()


class ExperimentSpecification(DomainModel):
    """Reusable definition of what experiment should be executed."""

    id: NonEmptyStr
    name: NonEmptyStr
    description: NonEmptyStr
    task_type: ExperimentTaskType
    repository: RepositorySource
    dataset: DatasetSource | None = None
    entrypoint: NonEmptyStr | None = None
    command: tuple[NonEmptyStr, ...] = ()
    resolved_command: ExecutableCommand | None = None
    environment: EnvironmentSpecification = Field(default_factory=EnvironmentSpecification)
    dataset_requirement: DatasetRequirement | None = None
    environment_requirement: EnvironmentRequirement = Field(default_factory=EnvironmentRequirement)
    resource_requirement: ResourceRequirement = Field(default_factory=ResourceRequirement)
    hyperparameters: dict[NonEmptyStr, JsonValue] = Field(default_factory=dict)
    expected_metrics: tuple[MetricExpectation, ...] = ()
    expected_claim_ids: tuple[NonEmptyStr,...] = ()
    provenance_decision_ids: tuple[NonEmptyStr,...] = ()
    seed: int | None = None
    tags: tuple[NonEmptyStr, ...] = ()
    metadata: dict[NonEmptyStr, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_execution_target(self) -> ExperimentSpecification:
        if self.entrypoint is None and not self.command and self.resolved_command is None:
            raise ValueError("entrypoint or command is required")
        if len(set(self.tags)) != len(self.tags):
            raise ValueError("tags must be unique")
        if len(set(self.expected_claim_ids))!=len(self.expected_claim_ids):raise ValueError("expected claim ids must be unique")
        if len(set(self.provenance_decision_ids))!=len(self.provenance_decision_ids):raise ValueError("provenance decision ids must be unique")
        return self


class RunError(DomainModel):
    """Serializable failure information shared by run state and result models."""

    code: NonEmptyStr
    message: NonEmptyStr
    retryable: bool = False
    details: dict[NonEmptyStr, JsonValue] = Field(default_factory=dict)


class ExperimentRun(DomainModel):
    """State of one concrete attempt to execute an experiment specification."""

    run_id: NonEmptyStr
    experiment_id: NonEmptyStr
    status: RunStatus = RunStatus.PENDING
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: RunError | None = None
    attempt: int = Field(default=1, ge=1)
    runtime: NonEmptyStr | None = None

    @field_validator("created_at", "started_at", "finished_at")
    @classmethod
    def timestamps_are_aware(cls, value: datetime | None, info: object) -> datetime | None:
        return _require_aware(value, info.field_name)

    @model_validator(mode="after")
    def validate_state(self) -> ExperimentRun:
        if self.started_at is not None and self.started_at < self.created_at:
            raise ValueError("started_at cannot precede created_at")
        if self.finished_at is not None:
            lower_bound = self.started_at or self.created_at
            if self.finished_at < lower_bound:
                raise ValueError("finished_at cannot precede run start")
        if self.status is RunStatus.FAILED and self.error is None:
            raise ValueError("failed runs require error details")
        if self.status is RunStatus.SUCCEEDED and self.error is not None:
            raise ValueError("successful runs cannot contain an error")
        return self


class ResourceRequest(DomainModel):
    """Requested capacity, independent of any specific scheduler or GPU vendor."""

    cpu_cores: float = Field(default=1.0, gt=0, allow_inf_nan=False)
    memory_mb: int = Field(default=1024, ge=128)
    gpu_count: int = Field(default=0, ge=0)


class RuntimeOptions(DomainModel):
    """Small set of execution controls understood at the runtime boundary."""

    timeout_seconds: int = Field(default=3600, gt=0)
    network_access: bool = False


class RunRequest(DomainModel):
    """Self-contained platform request submitted to an experiment runtime."""

    run_id: NonEmptyStr
    experiment: ExperimentSpecification
    repository_source: RepositorySource
    dataset_source: DatasetSource | None = None
    environment: EnvironmentSpecification = Field(default_factory=EnvironmentSpecification)
    resources: ResourceRequest = Field(default_factory=ResourceRequest)
    runtime_options: RuntimeOptions = Field(default_factory=RuntimeOptions)
    planner_decisions: tuple[SerializeAsAny[DomainModel], ...] = ()
    expected_claims: tuple[SerializeAsAny[DomainModel], ...] = ()

    @model_validator(mode="after")
    def references_match_experiment(self) -> RunRequest:
        decision_ids = {
            getattr(item, "decision_id", None) for item in self.planner_decisions
        }
        claim_ids = {getattr(item, "id", None) for item in self.expected_claims}
        if not set(self.experiment.provenance_decision_ids) <= decision_ids:
            raise ValueError("run request is missing planner decisions")
        if not set(self.experiment.expected_claim_ids) <= claim_ids:
            raise ValueError("run request is missing expected claims")
        if self.repository_source != self.experiment.repository:
            raise ValueError(
                "run request repository source must match the experiment specification"
            )
        return self


class Metric(DomainModel):
    """One structured observed metric emitted by an experiment run."""

    name: NonEmptyStr
    value: float = Field(allow_inf_nan=False)
    step: int | None = Field(default=None, ge=0)
    split: NonEmptyStr | None = None
    unit: NonEmptyStr | None = None
    metadata: dict[NonEmptyStr, JsonValue] = Field(default_factory=dict)

    @field_validator("value", mode="before")
    @classmethod
    def reject_boolean_value(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("boolean values are not valid metrics")
        return value


class Artifact(DomainModel):
    """Reference to a produced artifact; this model performs no storage or IO."""

    name: NonEmptyStr
    kind: ArtifactKind
    uri: NonEmptyStr
    media_type: NonEmptyStr | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    checksum: NonEmptyStr | None = None
    metadata: dict[NonEmptyStr, JsonValue] = Field(default_factory=dict)


class RunStartedPayload(DomainModel):
    runtime: NonEmptyStr


class StatusChangedPayload(DomainModel):
    previous_status: RunStatus | None = None
    status: RunStatus
    message: NonEmptyStr | None = None


class AgentEventPayload(DomainModel):
    agent_name: NonEmptyStr
    message: NonEmptyStr | None = None
    experiment_id: NonEmptyStr | None = None
    component_type: NonEmptyStr = "agent"

class PlanEventPayload(DomainModel):
    experiment_id:NonEmptyStr;plan_id:NonEmptyStr;summary:NonEmptyStr;revision:int=Field(default=1,ge=1)

class CommandEventPayload(DomainModel):
    experiment_id:NonEmptyStr;command_id:NonEmptyStr;program:NonEmptyStr;status:NonEmptyStr;exit_code:int|None=None;duration_seconds:float|None=Field(default=None,ge=0)

class PatchEventPayload(DomainModel):
    experiment_id:NonEmptyStr;patch_id:NonEmptyStr;summary:NonEmptyStr;accepted:bool

class ValidationEventPayload(DomainModel):
    experiment_id:NonEmptyStr;validator_name:NonEmptyStr;valid:bool;status:NonEmptyStr;violations:tuple[NonEmptyStr,...]=()


class LogPayload(DomainModel):
    level: NonEmptyStr = "INFO"
    message: NonEmptyStr
    stream: NonEmptyStr | None = None


class RunTerminalPayload(DomainModel):
    status: RunStatus
    exit_code: int | None = None
    error: RunError | None = None

    @model_validator(mode="after")
    def require_terminal_status(self) -> RunTerminalPayload:
        if self.status not in {
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }:
            raise ValueError("terminal payload requires a terminal status")
        if self.status is RunStatus.FAILED and self.error is None:
            raise ValueError("failed terminal payload requires error details")
        return self


RunEventPayload = (
    RunStartedPayload
    | StatusChangedPayload
    | AgentEventPayload
    | PlanEventPayload
    | CommandEventPayload
    | PatchEventPayload
    | ValidationEventPayload
    | LogPayload
    | Metric
    | Artifact
    | RunTerminalPayload
)


class RunEvent(DomainModel):
    """Typed event produced during runtime execution."""

    run_id: NonEmptyStr
    event_type: EventType
    timestamp: datetime = Field(default_factory=utc_now)
    payload: RunEventPayload

    @field_validator("timestamp")
    @classmethod
    def timestamp_is_aware(cls, value: datetime) -> datetime:
        return _require_aware(value, "timestamp")  # type: ignore[return-value]

    @model_validator(mode="after")
    def payload_matches_event_type(self) -> RunEvent:
        expected: dict[EventType, type[DomainModel]] = {
            EventType.RUN_STARTED: RunStartedPayload,
            EventType.RUN_STATUS_CHANGED: StatusChangedPayload,
            EventType.AGENT_STARTED: AgentEventPayload,
            EventType.AGENT_FINISHED: AgentEventPayload,
            EventType.PLAN_CREATED: PlanEventPayload,
            EventType.PLAN_UPDATED: PlanEventPayload,
            EventType.COMMAND_STARTED: CommandEventPayload,
            EventType.COMMAND_FINISHED: CommandEventPayload,
            EventType.PATCH_CREATED: PatchEventPayload,
            EventType.VALIDATION_RESULT: ValidationEventPayload,
            EventType.LOG: LogPayload,
            EventType.METRIC: Metric,
            EventType.ARTIFACT_CREATED: Artifact,
            EventType.RUN_FINISHED: RunTerminalPayload,
            EventType.RUN_FAILED: RunTerminalPayload,
        }
        payload_type = expected[self.event_type]
        if not isinstance(self.payload, payload_type):
            raise ValueError(
                f"{self.event_type.value} requires {payload_type.__name__} payload"
            )
        if self.event_type is EventType.RUN_FAILED:
            if self.payload.status is not RunStatus.FAILED:  # type: ignore[union-attr]
                raise ValueError("run_failed requires failed terminal status")
        if self.event_type is EventType.RUN_FINISHED:
            if self.payload.status is RunStatus.FAILED:  # type: ignore[union-attr]
                raise ValueError("failed runs must use run_failed")
        return self


class RunResult(DomainModel):
    """Structured terminal outcome returned by an experiment runtime."""

    run_id: NonEmptyStr
    status: RunStatus
    metrics: tuple[Metric, ...] = ()
    artifacts: tuple[Artifact, ...] = ()
    error: RunError | None = None
    exit_code: int | None = None
    started_at: datetime | None = None
    finished_at: datetime
    metadata: dict[NonEmptyStr, JsonValue] = Field(default_factory=dict)

    @field_validator("started_at", "finished_at")
    @classmethod
    def result_timestamps_are_aware(
        cls, value: datetime | None, info: object
    ) -> datetime | None:
        return _require_aware(value, info.field_name)

    @model_validator(mode="after")
    def validate_terminal_result(self) -> RunResult:
        if self.status not in {
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }:
            raise ValueError("RunResult requires a terminal status")
        if self.started_at is not None and self.finished_at < self.started_at:
            raise ValueError("finished_at cannot precede started_at")
        if self.status is RunStatus.SUCCEEDED:
            if self.error is not None:
                raise ValueError("successful results cannot contain an error")
            if self.exit_code != 0:
                raise ValueError("successful results require exit_code 0")
        if self.status is RunStatus.FAILED and self.error is None:
            raise ValueError("failed results require error details")
        return self
