"""Persistent product-facing intake and reproduction event models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import Field, JsonValue, field_validator, model_validator

from .alignment import PaperCodeAlignmentCatalog
from .experiment import DomainModel, NonEmptyStr, _require_aware, utc_now
from .external_resources import ResourceResolutionReport
from .intelligence import GoalResolutionResult, PaperExperimentCatalog
from .planner import ReproductionExecutionPlan
from .repository import RepositoryAnalysisCatalog
from .reproduction import PaperReference


class ReproductionIntakeState(str, Enum):
    ANALYZING = "analyzing"
    AMBIGUOUS = "ambiguous"
    WAITING_FOR_RESOURCE = "waiting_for_resource"
    READY_TO_RUN = "ready_to_run"
    QUEUED = "queued"
    RUNNING = "running"
    TERMINAL = "terminal"


class ReproductionEventType(str, Enum):
    PAPER_ANALYSIS_STARTED = "PAPER_ANALYSIS_STARTED"
    PAPER_ANALYSIS_COMPLETED = "PAPER_ANALYSIS_COMPLETED"
    REPOSITORY_ANALYSIS_STARTED = "REPOSITORY_ANALYSIS_STARTED"
    REPOSITORY_ANALYSIS_COMPLETED = "REPOSITORY_ANALYSIS_COMPLETED"
    EXPERIMENT_SELECTION_RESOLVED = "EXPERIMENT_SELECTION_RESOLVED"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    RESOURCE_REQUIRED = "RESOURCE_REQUIRED"
    RESOURCE_RESOLVED = "RESOURCE_RESOLVED"
    PLANNING_STARTED = "PLANNING_STARTED"
    PLANNING_COMPLETED = "PLANNING_COMPLETED"
    JOB_QUEUED = "JOB_QUEUED"
    JOB_CLAIMED = "JOB_CLAIMED"
    GPU_WAITING = "GPU_WAITING"
    GPU_ALLOCATED = "GPU_ALLOCATED"
    STEP_STARTED = "STEP_STARTED"
    STEP_COMPLETED = "STEP_COMPLETED"
    EPOCH_PROGRESS = "EPOCH_PROGRESS"
    AGENT_PATCH_STARTED = "AGENT_PATCH_STARTED"
    AGENT_PATCH_COMPLETED = "AGENT_PATCH_COMPLETED"
    GPU_OOM = "GPU_OOM"
    RESOURCE_ADAPTED = "RESOURCE_ADAPTED"
    STEP_RETRYING = "STEP_RETRYING"
    FINAL_RESULT_ACQUIRED = "FINAL_RESULT_ACQUIRED"
    COMPARISON_COMPLETED = "COMPARISON_COMPLETED"
    JOB_SUCCEEDED = "JOB_SUCCEEDED"
    JOB_FAILED = "JOB_FAILED"
    JOB_CANCELLED = "JOB_CANCELLED"


_FORBIDDEN_EVENT_KEYS = {
    "chain_of_thought", "private_reasoning", "reasoning_tokens", "host_path",
    "docker_socket", "lease_token", "secret", "password", "access_token",
}


class ReproductionEvent(DomainModel):
    event_id: NonEmptyStr
    sequence: int = Field(ge=1)
    intake_id: NonEmptyStr
    job_id: NonEmptyStr | None = None
    owner_principal: NonEmptyStr
    event_type: ReproductionEventType
    payload: dict[NonEmptyStr, JsonValue] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("created_at")
    @classmethod
    def aware_time(cls, value: datetime) -> datetime:
        return _require_aware(value, "created_at")  # type: ignore[return-value]

    @field_validator("payload")
    @classmethod
    def product_payload_only(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        def inspect(item: object) -> None:
            if isinstance(item, dict):
                for key, child in item.items():
                    if key.lower() in _FORBIDDEN_EVENT_KEYS:
                        raise ValueError(f"event payload contains forbidden private field: {key}")
                    inspect(child)
            elif isinstance(item, list):
                for child in item:
                    inspect(child)

        inspect(value)
        return value


class ReproductionIntake(DomainModel):
    intake_id: NonEmptyStr
    owner_principal: NonEmptyStr
    source_filename: NonEmptyStr
    repository_url: NonEmptyStr
    user_goal: NonEmptyStr
    state: ReproductionIntakeState
    paper: PaperReference | None = None
    paper_catalog: PaperExperimentCatalog | None = None
    repository_catalog: RepositoryAnalysisCatalog | None = None
    alignment_catalog: PaperCodeAlignmentCatalog | None = None
    goal_resolution: GoalResolutionResult | None = None
    resource_resolution: ResourceResolutionReport | None = None
    execution_plan: ReproductionExecutionPlan | None = None
    clarification_answers: tuple[NonEmptyStr, ...] = ()
    waiting_reason: NonEmptyStr | None = None
    job_id: NonEmptyStr | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("created_at", "updated_at")
    @classmethod
    def aware_times(cls, value: datetime, info: object) -> datetime:
        return _require_aware(value, info.field_name)  # type: ignore[return-value,union-attr]

    @model_validator(mode="after")
    def consistent(self):
        if self.updated_at < self.created_at:
            raise ValueError("intake updated_at cannot precede created_at")
        if self.resource_resolution is not None and self.resource_resolution.principal != self.owner_principal:
            raise ValueError("resource resolution owner differs from intake owner")
        if self.resource_resolution is not None and self.resource_resolution.intake_id != self.intake_id:
            raise ValueError("resource resolution belongs to another intake")
        if self.state is ReproductionIntakeState.READY_TO_RUN:
            if self.execution_plan is None or self.job_id is None:
                raise ValueError("ready intake requires an execution plan and durable job")
        return self
