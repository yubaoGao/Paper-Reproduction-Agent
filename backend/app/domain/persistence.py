"""Persistence-facing domain records without database dependencies."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import Field, field_validator, model_validator

from .comparison import ReproductionComparisonReport
from .experiment import DomainModel, FinalResult, NonEmptyStr, _require_aware, utc_now
from .intelligence import ExperimentSelection
from .planner import ReproductionExecutionPlan
from .reproduction import PaperReference, ReproductionSpecification


class ReproductionJobStatus(str, Enum):
    """Job/planning and durable queue lifecycle, distinct from RunStatus."""

    PENDING = "pending"
    PLANNING = "planning"
    READY = "ready"
    QUEUED = "queued"
    CLAIMED = "claimed"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"


class ResultValidationStatus(str, Enum):
    PENDING = "pending"
    VALID = "valid"
    INVALID = "invalid"


class ReproductionJob(DomainModel):
    job_id: NonEmptyStr
    paper: PaperReference
    user_goal: NonEmptyStr
    selection: ExperimentSelection
    status: ReproductionJobStatus = ReproductionJobStatus.PENDING
    enqueued_at: datetime | None = None
    worker_id: NonEmptyStr | None = None
    lease_token: NonEmptyStr | None = None
    claimed_at: datetime | None = None
    lease_expires_at: datetime | None = None
    heartbeat_at: datetime | None = None
    claim_count: int = Field(default=0, ge=0)
    last_error: NonEmptyStr | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator(
        "enqueued_at", "claimed_at", "lease_expires_at", "heartbeat_at",
        "created_at", "updated_at",
    )
    @classmethod
    def aware_times(cls, value: datetime | None, info: object) -> datetime | None:
        return _require_aware(value, info.field_name)  # type: ignore[return-value,union-attr]

    @model_validator(mode="after")
    def timestamps_ordered(self):
        if self.updated_at < self.created_at:
            raise ValueError("job updated_at cannot precede created_at")
        if self.user_goal != self.selection.original_user_goal:
            raise ValueError("job goal must match the authoritative experiment selection")
        lease_values = (
            self.worker_id,
            self.lease_token,
            self.claimed_at,
            self.lease_expires_at,
            self.heartbeat_at,
        )
        if any(value is not None for value in lease_values) and any(value is None for value in lease_values):
            raise ValueError("job lease ownership fields must be present or absent together")
        if self.status in {ReproductionJobStatus.CLAIMED, ReproductionJobStatus.RUNNING} and any(
            value is None for value in lease_values
        ):
            raise ValueError("claimed or running job requires complete lease ownership")
        if self.status is ReproductionJobStatus.QUEUED and self.enqueued_at is None:
            raise ValueError("queued job requires enqueued_at")
        if self.claimed_at and self.heartbeat_at and self.heartbeat_at < self.claimed_at:
            raise ValueError("job heartbeat cannot precede claim")
        if self.claimed_at and self.lease_expires_at and self.lease_expires_at <= self.claimed_at:
            raise ValueError("job lease must expire after it was claimed")
        return self


class AuthoritativePlanningSnapshot(DomainModel):
    snapshot_id: NonEmptyStr
    job_id: NonEmptyStr
    specification: ReproductionSpecification
    execution_plan: ReproductionExecutionPlan
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("created_at")
    @classmethod
    def aware_time(cls, value: datetime) -> datetime:
        return _require_aware(value, "created_at")  # type: ignore[return-value]

    @model_validator(mode="after")
    def identities_align(self):
        if self.execution_plan.reproduction_specification_id != self.specification.id:
            raise ValueError("planning snapshot plan and specification identities differ")
        if tuple(self.execution_plan.target_experiment_ids) != tuple(self.specification.selected_experiment_ids):
            raise ValueError("planning snapshot experiment scope differs from specification")
        return self


class PersistedFinalResult(DomainModel):
    job_id: NonEmptyStr
    run_id: NonEmptyStr
    result: FinalResult
    validation_status: ResultValidationStatus = ResultValidationStatus.PENDING
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("created_at", "updated_at")
    @classmethod
    def aware_times(cls, value: datetime, info: object) -> datetime:
        return _require_aware(value, info.field_name)  # type: ignore[return-value,union-attr]

    @model_validator(mode="after")
    def timestamps_ordered(self):
        if self.updated_at < self.created_at:
            raise ValueError("result updated_at cannot precede created_at")
        return self


class PersistedComparisonReport(DomainModel):
    job_id: NonEmptyStr
    report: ReproductionComparisonReport
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("created_at", "updated_at")
    @classmethod
    def aware_times(cls, value: datetime, info: object) -> datetime:
        return _require_aware(value, info.field_name)  # type: ignore[return-value,union-attr]

    @model_validator(mode="after")
    def timestamps_ordered(self):
        if self.updated_at < self.created_at:
            raise ValueError("comparison updated_at cannot precede created_at")
        return self
