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


class CheckpointPolicy(str, Enum):
    BEST_METRIC = "best_metric"
    FINAL_EPOCH = "final_epoch"
    EARLY_STOPPED = "early_stopped"
    FIXED = "fixed"
    UNKNOWN = "unknown"


class MetricDirection(str, Enum):
    MAXIMIZE = "maximize"
    MINIMIZE = "minimize"


class ResultAggregation(str, Enum):
    NONE = "none"
    MEAN = "mean"
    MEAN_STD = "mean_std"


class EvaluationPolicySource(str, Enum):
    PAPER_EXPLICIT = "paper_explicit"
    CODE_EXPLICIT = "code_explicit"
    SCIENTIFIC_DEFAULT = "scientific_default"
    USER_OVERRIDE = "user_override"


class EvaluationPolicyConfidence(str, Enum):
    EXPLICIT = "explicit"
    INFERRED = "inferred"


class EvaluationPolicy(DomainModel):
    """How one selected paper experiment obtains its canonical final result."""

    checkpoint_policy: CheckpointPolicy
    selection_metric: NonEmptyStr | None = None
    selection_split: NonEmptyStr | None = None
    direction: MetricDirection | None = None
    reporting_split: NonEmptyStr | None = None
    reporting_metrics: tuple[NonEmptyStr, ...] = ()
    run_count: int = Field(default=1, ge=1)
    seeds: tuple[int, ...] = ()
    aggregation: ResultAggregation = ResultAggregation.NONE
    source: EvaluationPolicySource
    fixed_checkpoint: NonEmptyStr | None = None
    fixed_epoch: int | None = Field(default=None, ge=0)
    evidence: tuple[JsonValue, ...] = ()
    confidence: EvaluationPolicyConfidence | float | None = None
    warnings: tuple[NonEmptyStr, ...] = ()

    @field_validator("confidence", mode="before")
    @classmethod
    def reject_boolean_confidence(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("confidence must be numeric")
        if isinstance(value, (int, float)) and not 0 <= value <= 1:
            raise ValueError("numeric confidence must be between zero and one")
        return value

    @model_validator(mode="after")
    def valid_policy(self):
        if len(set(self.reporting_metrics)) != len(self.reporting_metrics):
            raise ValueError("reporting metrics must be unique")
        if len(set(self.seeds)) != len(self.seeds):
            raise ValueError("seeds must be unique")
        if self.seeds and len(self.seeds) != self.run_count:
            raise ValueError("explicit seeds must exactly cover run_count")
        if self.run_count == 1 and self.aggregation is not ResultAggregation.NONE:
            raise ValueError("single-run evaluation cannot aggregate")
        if self.run_count > 1 and self.aggregation is ResultAggregation.NONE:
            raise ValueError("multiple runs require mean or mean/std aggregation")
        selection = (self.selection_metric, self.selection_split, self.direction)
        if self.checkpoint_policy in {
            CheckpointPolicy.BEST_METRIC,
            CheckpointPolicy.EARLY_STOPPED,
        } and any(value is None for value in selection):
            raise ValueError("metric-based checkpoint policy requires metric, split and direction")
        if self.checkpoint_policy is CheckpointPolicy.UNKNOWN and any(
            value is not None for value in selection
        ):
            raise ValueError("unknown checkpoint policy cannot assert a selection rule")
        if self.checkpoint_policy is CheckpointPolicy.FIXED and not (
            self.fixed_checkpoint or self.fixed_epoch is not None
        ):
            raise ValueError("fixed checkpoint policy requires checkpoint or epoch")
        if self.checkpoint_policy is not CheckpointPolicy.FIXED and (
            self.fixed_checkpoint is not None or self.fixed_epoch is not None
        ):
            raise ValueError("fixed checkpoint fields require FIXED policy")
        has_asserted_behavior = bool(
            self.checkpoint_policy is not CheckpointPolicy.UNKNOWN
            or any(value is not None for value in selection)
            or self.reporting_split
            or self.reporting_metrics
            or self.run_count != 1
            or self.seeds
            or self.aggregation is not ResultAggregation.NONE
            or self.fixed_checkpoint
            or self.fixed_epoch is not None
        )
        if self.source in {
            EvaluationPolicySource.PAPER_EXPLICIT,
            EvaluationPolicySource.CODE_EXPLICIT,
        } and has_asserted_behavior and not self.evidence:
            raise ValueError("explicit evaluation policy requires evidence")
        expected_source = {
            EvaluationPolicySource.PAPER_EXPLICIT: "paper",
            EvaluationPolicySource.CODE_EXPLICIT: "repository",
        }.get(self.source)
        if expected_source and self.evidence:
            if any(
                not isinstance(item, dict)
                or item.get("source_type") != expected_source
                or not any(item.get(key) for key in ("source_id", "locator", "text"))
                for item in self.evidence
            ):
                raise ValueError("explicit evaluation policy evidence has the wrong source or shape")
        if self.source is EvaluationPolicySource.SCIENTIFIC_DEFAULT and not self.warnings:
            raise ValueError("scientific default must carry an explicit warning")
        if self.source is EvaluationPolicySource.SCIENTIFIC_DEFAULT:
            if self.confidence is not EvaluationPolicyConfidence.INFERRED:
                raise ValueError("scientific default confidence must be INFERRED")
            if "INFERRED_EVALUATION_POLICY" not in self.warnings:
                raise ValueError("scientific default requires INFERRED_EVALUATION_POLICY warning")
        return self

    @property
    def is_resolved(self) -> bool:
        return bool(
            self.checkpoint_policy is not CheckpointPolicy.UNKNOWN
            and self.reporting_split
            and self.reporting_metrics
        )


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
class ParameterApplication(str,Enum): CLI_ARGUMENT="cli_argument"; CONFIG_VALUE="config_value"; ENVIRONMENT_VARIABLE="environment_variable"
class AppliedParameter(DomainModel):
    name:NonEmptyStr; value:JsonValue; application:ParameterApplication; reference:NonEmptyStr
class ExecutableCommand(DomainModel):
    """Argument-vector command declaration; never a shell expression."""
    program:NonEmptyStr;arguments:tuple[NonEmptyStr,...]=();working_directory:NonEmptyStr=".";environment_variable_references:tuple[NonEmptyStr,...]=();entrypoint_id:NonEmptyStr|None=None;config_ids:tuple[NonEmptyStr,...]=();command_reference_id:NonEmptyStr|None=None;applied_parameters:tuple[AppliedParameter,...]=()
    @model_validator(mode="after")
    def reject_shell_syntax(self):
        unsafe={"&&","||",";","|",">","<"}
        if self.program in unsafe or any(x in unsafe or "\n" in x or "\r" in x for x in self.arguments):raise ValueError("structured command cannot contain shell control operators")
        if len({x.name for x in self.applied_parameters})!=len(self.applied_parameters):raise ValueError("applied command parameter names must be unique")
        return self
class DatasetRequirement(DomainModel):
    name:NonEmptyStr;repository_dataset_id:NonEmptyStr|None=None;binding:NonEmptyStr|None=None;split:NonEmptyStr|None=None;preprocessing_assumptions:tuple[NonEmptyStr,...]=();loader_references:tuple[NonEmptyStr,...]=();paper_evidence:tuple[SerializeAsAny[DomainModel],...]=();repository_evidence:tuple[SerializeAsAny[DomainModel],...]=();availability:DatasetAvailability
    @model_validator(mode="after")
    def binding_matches_status(self):
        if self.availability is DatasetAvailability.AVAILABLE and self.binding is None:raise ValueError("available dataset requires a binding")
        return self
class EnvironmentRequirement(DomainModel):
    python_constraint:NonEmptyStr|None=None;dependencies:tuple[NonEmptyStr,...]=();system_dependencies:tuple[NonEmptyStr,...]=();frameworks:tuple[NonEmptyStr,...]=();cuda_hints:tuple[NonEmptyStr,...]=();manifest_references:tuple[NonEmptyStr,...]=();install_commands:tuple[NonEmptyStr,...]=()
class ResourceRequirement(DomainModel):
    gpu_required:bool|None=None;gpu_count:int|None=Field(default=None,ge=0);cpu_cores:float|None=Field(default=None,gt=0);memory_mb:int|None=Field(default=None,ge=128);notes:tuple[NonEmptyStr,...]=()


class ExperimentActionType(str, Enum):
    TRAIN = "train"
    EVALUATE = "evaluate"
    AGGREGATE = "aggregate"


class ExperimentAction(DomainModel):
    action_id: NonEmptyStr
    paper_experiment_id: NonEmptyStr
    action_type: ExperimentActionType
    depends_on_action_ids: tuple[NonEmptyStr, ...] = ()
    command: ExecutableCommand | None = None
    seed: int | None = None
    produces_checkpoint: bool = False
    produces_run_result: bool = False
    produces_final_result: bool = False

    @model_validator(mode="after")
    def valid_action(self):
        if self.action_id in self.depends_on_action_ids:
            raise ValueError("action cannot depend on itself")
        if len(set(self.depends_on_action_ids)) != len(self.depends_on_action_ids):
            raise ValueError("action dependencies must be unique")
        if self.action_type in {ExperimentActionType.TRAIN, ExperimentActionType.EVALUATE}:
            if self.command is None:
                raise ValueError("train and evaluate actions require a structured command")
        if self.action_type is ExperimentActionType.AGGREGATE:
            if self.command is not None or self.seed is not None:
                raise ValueError("aggregate action is deterministic and has no command or seed")
            if not self.produces_final_result:
                raise ValueError("aggregate action must produce FinalResult")
        return self


class ExperimentActionPlan(DomainModel):
    paper_experiment_id: NonEmptyStr
    actions: tuple[ExperimentAction, ...] = Field(min_length=1)
    execution_order: tuple[NonEmptyStr, ...] = Field(min_length=1)
    final_action_id: NonEmptyStr

    @model_validator(mode="after")
    def valid_dag(self):
        ids = [item.action_id for item in self.actions]
        if len(ids) != len(set(ids)):
            raise ValueError("action IDs must be unique")
        if set(ids) != set(self.execution_order) or len(ids) != len(self.execution_order):
            raise ValueError("action execution order must cover every action exactly")
        if self.final_action_id not in ids:
            raise ValueError("final action is absent from action plan")
        positions = {value: index for index, value in enumerate(self.execution_order)}
        for action in self.actions:
            if action.paper_experiment_id != self.paper_experiment_id:
                raise ValueError("action changes the selected paper experiment")
            if not set(action.depends_on_action_ids) <= set(ids):
                raise ValueError("action dependency references an unknown action")
            if any(positions[parent] >= positions[action.action_id] for parent in action.depends_on_action_ids):
                raise ValueError("action execution order violates dependencies")
        final = next(item for item in self.actions if item.action_id == self.final_action_id)
        if not final.produces_final_result:
            raise ValueError("final action must produce FinalResult")
        return self


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
    evaluation_policy: EvaluationPolicy | None = None
    action_plan: ExperimentActionPlan | None = None
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
        if self.action_plan is not None:
            paper_id = self.metadata.get("paper_experiment_id")
            if paper_id is not None and self.action_plan.paper_experiment_id != paper_id:
                raise ValueError("action plan changes the selected paper experiment")
            if self.evaluation_policy is None or not self.evaluation_policy.is_resolved:
                raise ValueError("action plan requires a resolved evaluation policy")
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


class FinalMetricStatus(str, Enum):
    AVAILABLE = "available"
    MISSING = "missing"
    UNAVAILABLE = "unavailable"


class FinalMetric(DomainModel):
    name: NonEmptyStr
    status: FinalMetricStatus = FinalMetricStatus.AVAILABLE
    value: float | None = Field(default=None, allow_inf_nan=False)
    split: NonEmptyStr
    unit: NonEmptyStr | None = None
    checkpoint_reference: NonEmptyStr | None = None
    epoch: int | None = Field(default=None, ge=0)
    std: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    evidence: tuple[JsonValue, ...] = ()
    provenance: dict[NonEmptyStr, JsonValue] = Field(default_factory=dict)

    @field_validator("value", "std", mode="before")
    @classmethod
    def reject_boolean_numbers(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("boolean values are not valid final metrics")
        return value

    @model_validator(mode="after")
    def value_matches_status(self):
        if self.status is FinalMetricStatus.AVAILABLE and self.value is None:
            raise ValueError("available final metric requires a value")
        if self.status is not FinalMetricStatus.AVAILABLE and self.value is not None:
            raise ValueError("missing or unavailable final metric cannot assert a value")
        if self.status is not FinalMetricStatus.AVAILABLE and self.std is not None:
            raise ValueError("missing or unavailable final metric cannot assert a standard deviation")
        return self


class RunFinalResult(DomainModel):
    result_id: NonEmptyStr
    run_id: NonEmptyStr
    seed: int | None = None
    selected_checkpoint: NonEmptyStr | None = None
    selected_epoch: int | None = Field(default=None, ge=0)
    selection_metric: NonEmptyStr | None = None
    selection_split: NonEmptyStr | None = None
    selection_value: float | None = Field(default=None, allow_inf_nan=False)
    reporting_metrics: tuple[FinalMetric, ...] = Field(min_length=1)
    artifact_references: tuple[NonEmptyStr, ...] = ()
    evidence: tuple[JsonValue, ...] = ()
    provenance: dict[NonEmptyStr, JsonValue] = Field(default_factory=dict)

    @field_validator("selection_value", mode="before")
    @classmethod
    def reject_boolean_selection_value(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("boolean selection value is invalid")
        return value

    @model_validator(mode="after")
    def one_checkpoint(self):
        names = [item.name for item in self.reporting_metrics]
        if len(names) != len(set(names)):
            raise ValueError("run final metrics must be unique")
        if len(self.artifact_references) != len(set(self.artifact_references)):
            raise ValueError("run artifact references must be unique")
        if len({item.checkpoint_reference for item in self.reporting_metrics}) != 1:
            raise ValueError("run final metrics cannot be combined across checkpoints")
        if len({item.epoch for item in self.reporting_metrics}) != 1:
            raise ValueError("run final metrics cannot be combined across epochs")
        if self.selected_checkpoint is not None and any(
            item.checkpoint_reference != self.selected_checkpoint
            for item in self.reporting_metrics
        ):
            raise ValueError("all final metrics must come from the selected checkpoint")
        if self.selected_epoch is not None and any(
            item.epoch != self.selected_epoch for item in self.reporting_metrics
        ):
            raise ValueError("all final metrics must come from the selected epoch")
        return self


class FinalResult(DomainModel):
    result_id: NonEmptyStr
    paper_experiment_id: NonEmptyStr
    evaluation_policy: EvaluationPolicy
    runs: tuple[RunFinalResult, ...] = Field(min_length=1)
    reporting_metrics: tuple[FinalMetric, ...] = Field(min_length=1)
    aggregation: ResultAggregation
    evidence: tuple[JsonValue, ...] = ()
    provenance: dict[NonEmptyStr, JsonValue] = Field(default_factory=dict)

    @property
    def required_metrics(self) -> tuple[FinalMetric, ...]:
        by_name = {item.name: item for item in self.reporting_metrics}
        return tuple(by_name[name] for name in self.evaluation_policy.reporting_metrics if name in by_name)

    @property
    def additional_metrics(self) -> tuple[FinalMetric, ...]:
        required = set(self.evaluation_policy.reporting_metrics)
        return tuple(item for item in self.reporting_metrics if item.name not in required)

    @model_validator(mode="after")
    def canonical_result_matches_policy(self):
        policy = self.evaluation_policy
        metric_names = [item.name for item in self.reporting_metrics]
        if len(metric_names) != len(set(metric_names)):
            raise ValueError("canonical final metrics must be unique")
        if not policy.is_resolved:
            raise ValueError("FinalResult requires a resolved EvaluationPolicy")
        if self.aggregation is not policy.aggregation:
            raise ValueError("FinalResult aggregation differs from EvaluationPolicy")
        if len(self.runs) != policy.run_count:
            raise ValueError("FinalResult must include every configured run; best-seed selection is forbidden")
        if len({item.result_id for item in self.runs}) != len(self.runs):
            raise ValueError("FinalResult run result ids must be unique")
        if len({item.run_id for item in self.runs}) != len(self.runs):
            raise ValueError("FinalResult run ids must be unique")
        seeds = [item.seed for item in self.runs]
        if policy.seeds and tuple(seeds) != policy.seeds:
            raise ValueError("FinalResult seeds differ from EvaluationPolicy")
        if len([seed for seed in seeds if seed is not None]) != len(set(seed for seed in seeds if seed is not None)):
            raise ValueError("FinalResult contains duplicate seeds")
        expected_names = tuple(policy.reporting_metrics)
        for run in self.runs:
            if not (run.artifact_references or run.evidence or run.provenance):
                raise ValueError("per-run FinalResult requires artifact evidence or provenance")
            metrics_by_name = {item.name: item for item in run.reporting_metrics}
            if any(name not in metrics_by_name for name in expected_names):
                raise ValueError("per-run result omits a required reporting metric")
            if any(metrics_by_name[name].split != policy.reporting_split for name in expected_names):
                raise ValueError("per-run required reporting split differs from EvaluationPolicy")
            if any(not (item.evidence or item.provenance) for item in run.reporting_metrics):
                raise ValueError("final reporting metrics require evidence or provenance")
            if policy.checkpoint_policy in {CheckpointPolicy.BEST_METRIC, CheckpointPolicy.EARLY_STOPPED}:
                if (run.selection_metric, run.selection_split) != (
                    policy.selection_metric,
                    policy.selection_split,
                ) or run.selection_value is None:
                    raise ValueError("run checkpoint selection lacks policy provenance")
                if run.selected_checkpoint is None and run.selected_epoch is None:
                    raise ValueError("metric-selected run requires checkpoint or epoch provenance")
            if policy.checkpoint_policy is CheckpointPolicy.FINAL_EPOCH and run.selected_epoch is None:
                raise ValueError("final-epoch policy requires selected epoch provenance")
            if policy.checkpoint_policy is CheckpointPolicy.FIXED:
                if policy.fixed_checkpoint is not None and run.selected_checkpoint != policy.fixed_checkpoint:
                    raise ValueError("run does not use the fixed checkpoint")
                if policy.fixed_epoch is not None and run.selected_epoch != policy.fixed_epoch:
                    raise ValueError("run does not use the fixed epoch")
        canonical_by_name = {item.name: item for item in self.reporting_metrics}
        if any(name not in canonical_by_name for name in expected_names):
            raise ValueError("canonical result omits a required reporting metric")
        if any(canonical_by_name[name].split != policy.reporting_split for name in expected_names):
            raise ValueError("canonical required reporting split differs from EvaluationPolicy")
        if self.aggregation is ResultAggregation.NONE:
            if len(self.runs) != 1:
                raise ValueError("non-aggregated FinalResult requires one run")
            observed = self.runs[0].reporting_metrics
            canonical_values = tuple((item.name,item.status,item.value,item.split,item.unit) for item in self.reporting_metrics)
            observed_values = tuple((item.name,item.status,item.value,item.split,item.unit) for item in observed)
            if canonical_values != observed_values:
                raise ValueError("single-run canonical metrics must equal its run metrics")
        if self.aggregation is ResultAggregation.MEAN_STD and any(
            item.status is FinalMetricStatus.AVAILABLE and item.std is None for item in self.reporting_metrics
        ):
            raise ValueError("mean/std aggregation requires std for every metric")
        if self.aggregation is ResultAggregation.MEAN and any(
            item.std is not None for item in self.reporting_metrics
        ):
            raise ValueError("mean-only aggregation cannot report std")
        return self


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
