"""Production run aggregate for executing an authoritative reproduction plan."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import Field, JsonValue, field_validator, model_validator

from .experiment import (
    Artifact,
    DomainModel,
    FinalResult,
    ExperimentActionType,
    Metric,
    NonEmptyStr,
    RunStatus,
    _require_aware,
    utc_now,
)


class StepStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    PREPARING = "preparing"
    RUNNING = "running"
    VALIDATING = "validating"
    PATCHING = "patching"
    RETRYING = "retrying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class AttemptStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class FailureCategory(str, Enum):
    ENVIRONMENT = "environment"
    DEPENDENCY = "dependency"
    CODE = "code"
    CONFIG = "config"
    DATA = "data"
    RESOURCE = "resource"
    TIMEOUT = "timeout"
    VALIDATION = "validation"
    UNKNOWN = "unknown"


class ValidationPhase(str, Enum):
    DETERMINISTIC = "deterministic"
    SEMANTIC = "semantic"


class PatchStatus(str, Enum):
    APPLIED = "applied"
    REJECTED = "rejected"
    FAILED = "failed"


class FailureRecord(DomainModel):
    failure_id: NonEmptyStr
    category: FailureCategory
    code: NonEmptyStr
    message: NonEmptyStr
    retryable: bool
    details: dict[NonEmptyStr, JsonValue] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("created_at")
    @classmethod
    def aware_time(cls, value: datetime) -> datetime:
        return _require_aware(value, "created_at")  # type: ignore[return-value]


class ValidationRecord(DomainModel):
    validation_id: NonEmptyStr
    validator_name: NonEmptyStr
    phase: ValidationPhase
    passed: bool
    status: NonEmptyStr
    violations: tuple[NonEmptyStr, ...] = ()
    details: dict[NonEmptyStr, JsonValue] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("created_at")
    @classmethod
    def aware_time(cls, value: datetime) -> datetime:
        return _require_aware(value, "created_at")  # type: ignore[return-value]

    @model_validator(mode="after")
    def consistent(self):
        if self.passed and self.violations:
            raise ValueError("passed validation cannot contain violations")
        return self


class PatchRecord(DomainModel):
    patch_id: NonEmptyStr
    status: PatchStatus
    summary: NonEmptyStr
    changed_categories: tuple[NonEmptyStr, ...] = ()
    violations: tuple[NonEmptyStr, ...] = ()
    artifact: Artifact | None = None
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("created_at")
    @classmethod
    def aware_time(cls, value: datetime) -> datetime:
        return _require_aware(value, "created_at")  # type: ignore[return-value]

    @model_validator(mode="after")
    def consistent(self):
        if self.status is PatchStatus.APPLIED and self.violations:
            raise ValueError("applied patch cannot contain violations")
        return self


class ArtifactReference(DomainModel):
    step_id: NonEmptyStr
    attempt_number: int = Field(ge=1)
    artifact: Artifact


class AttemptRecord(DomainModel):
    attempt_number: int = Field(ge=1)
    command_id: NonEmptyStr
    status: AttemptStatus
    started_at: datetime
    finished_at: datetime
    exit_code: int | None = None
    stdout_reference: NonEmptyStr | None = None
    stderr_reference: NonEmptyStr | None = None
    failures: tuple[FailureRecord, ...] = ()
    validations: tuple[ValidationRecord, ...] = ()
    patches: tuple[PatchRecord, ...] = ()
    artifacts: tuple[ArtifactReference, ...] = ()
    metrics: tuple[Metric, ...] = ()
    final_result: FinalResult | None = None

    @field_validator("started_at", "finished_at")
    @classmethod
    def aware_times(cls, value: datetime, info: object) -> datetime:
        return _require_aware(value, info.field_name)  # type: ignore[return-value,union-attr]

    @model_validator(mode="after")
    def consistent(self):
        if self.finished_at < self.started_at:
            raise ValueError("attempt finished_at cannot precede started_at")
        if self.status is AttemptStatus.SUCCEEDED and self.failures:
            raise ValueError("successful attempt cannot contain failures")
        if self.status is AttemptStatus.SUCCEEDED and self.exit_code != 0:
            raise ValueError("successful attempt requires exit code zero")
        if self.status is AttemptStatus.TIMED_OUT and self.exit_code == 0:
            raise ValueError("timed-out attempt cannot have a successful exit code")
        if self.status in {AttemptStatus.FAILED, AttemptStatus.TIMED_OUT} and not self.failures:
            raise ValueError("failed or timed-out attempt requires a failure")
        return self


class StepRun(DomainModel):
    step_id: NonEmptyStr
    experiment_id: NonEmptyStr
    action_type: ExperimentActionType | None = None
    seed: int | None = None
    depends_on_step_ids: tuple[NonEmptyStr, ...] = ()
    priority: int = 0
    control_first: bool = False
    status: StepStatus = StepStatus.PENDING
    attempts: tuple[AttemptRecord, ...] = ()
    input_artifacts: tuple[ArtifactReference, ...] = ()
    artifacts: tuple[ArtifactReference, ...] = ()
    failure: FailureRecord | None = None
    analysis: JsonValue | None = None
    conclusion: NonEmptyStr | None = None
    final_result: FinalResult | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @field_validator("started_at", "finished_at")
    @classmethod
    def aware_times(cls, value: datetime | None, info: object) -> datetime | None:
        return _require_aware(value, info.field_name)  # type: ignore[union-attr]

    @model_validator(mode="after")
    def consistent(self):
        numbers = [item.attempt_number for item in self.attempts]
        if numbers != list(range(1, len(numbers) + 1)):
            raise ValueError("attempt history must be contiguous and append-only")
        terminal = {
            StepStatus.SUCCEEDED,
            StepStatus.FAILED,
            StepStatus.BLOCKED,
            StepStatus.CANCELLED,
        }
        if self.status in terminal and self.finished_at is None:
            raise ValueError("terminal step requires finished_at")
        if self.status is StepStatus.SUCCEEDED and self.failure is not None:
            raise ValueError("successful step cannot contain a failure")
        if self.status is StepStatus.SUCCEEDED and not self.attempts:
            raise ValueError("successful step requires an attempt")
        if self.status in {StepStatus.FAILED, StepStatus.BLOCKED} and self.failure is None:
            raise ValueError("failed or blocked step requires a failure")
        return self


class RunManifest(DomainModel):
    plan_id: NonEmptyStr
    reproduction_specification_id: NonEmptyStr
    repository_snapshot_id: NonEmptyStr
    resolved_commit_sha: NonEmptyStr
    ordered_step_ids: tuple[NonEmptyStr, ...]
    dependencies: dict[NonEmptyStr, tuple[NonEmptyStr, ...]]
    required_final_result_step_ids: tuple[NonEmptyStr, ...] = ()
    plan_digest: NonEmptyStr
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("created_at")
    @classmethod
    def aware_time(cls, value: datetime) -> datetime:
        return _require_aware(value, "created_at")  # type: ignore[return-value]

    @model_validator(mode="after")
    def valid_graph(self):
        step_ids = set(self.ordered_step_ids)
        if len(step_ids) != len(self.ordered_step_ids):
            raise ValueError("manifest step IDs must be unique")
        if set(self.dependencies) != step_ids:
            raise ValueError("manifest dependencies must cover every step")
        if len(self.required_final_result_step_ids)!=len(set(self.required_final_result_step_ids)):
            raise ValueError("manifest final-result step IDs must be unique")
        if not set(self.required_final_result_step_ids) <= step_ids:
            raise ValueError("manifest final-result requirement references unknown step")
        positions = {value: index for index, value in enumerate(self.ordered_step_ids)}
        for step_id, parents in self.dependencies.items():
            if not set(parents) <= step_ids:
                raise ValueError("manifest dependency references unknown step")
            if any(positions[parent] >= positions[step_id] for parent in parents):
                raise ValueError("manifest ordering violates dependencies")
        return self


class ReproductionRun(DomainModel):
    run_id: NonEmptyStr
    plan_id: NonEmptyStr
    status: RunStatus = RunStatus.PENDING
    manifest: RunManifest
    steps: tuple[StepRun, ...]
    artifacts: tuple[ArtifactReference, ...] = ()
    final_results: tuple[FinalResult, ...] = ()
    failure: FailureRecord | None = None
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    revision: int = Field(default=0, ge=0)

    @field_validator("created_at", "started_at", "finished_at")
    @classmethod
    def aware_times(cls, value: datetime | None, info: object) -> datetime | None:
        return _require_aware(value, info.field_name)  # type: ignore[union-attr]

    @model_validator(mode="after")
    def consistent(self):
        if self.plan_id != self.manifest.plan_id:
            raise ValueError("run and manifest plan IDs must match")
        step_ids = [item.step_id for item in self.steps]
        if tuple(step_ids) != self.manifest.ordered_step_ids:
            raise ValueError("run steps must exactly follow the authoritative manifest")
        for step in self.steps:
            if step.action_type is None and step.experiment_id != step.step_id:
                raise ValueError("legacy step and experiment IDs must match the execution plan")
            if step.depends_on_step_ids != self.manifest.dependencies[step.step_id]:
                raise ValueError("step dependencies differ from the authoritative manifest")
            if any(item.step_id != step.step_id for item in step.artifacts):
                raise ValueError("step artifact reference has the wrong owner")
        expected_results=tuple(step.final_result for step in self.steps if step.final_result is not None)
        if self.final_results!=expected_results:
            raise ValueError("run final-result index differs from step records")
        if self.status is RunStatus.SUCCEEDED and not all(
            item.status is StepStatus.SUCCEEDED for item in self.steps
        ):
            raise ValueError("successful run requires every step to succeed")
        if self.status is RunStatus.SUCCEEDED:
            required=set(self.manifest.required_final_result_step_ids)
            obtained={item.step_id for item in self.steps if item.final_result is not None}
            if not required<=obtained:raise ValueError("successful run is missing canonical FinalResult")
        if self.status is RunStatus.FAILED and self.failure is None:
            raise ValueError("failed reproduction run requires a failure")
        if self.status in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}:
            if self.finished_at is None:
                raise ValueError("terminal reproduction run requires finished_at")
        return self
