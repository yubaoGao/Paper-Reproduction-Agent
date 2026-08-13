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
    PENDING = "pending"
    PLANNING = "planning"
    READY = "ready"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
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
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("created_at", "updated_at")
    @classmethod
    def aware_times(cls, value: datetime, info: object) -> datetime:
        return _require_aware(value, info.field_name)  # type: ignore[return-value,union-attr]

    @model_validator(mode="after")
    def timestamps_ordered(self):
        if self.updated_at < self.created_at:
            raise ValueError("job updated_at cannot precede created_at")
        if self.user_goal != self.selection.original_user_goal:
            raise ValueError("job goal must match the authoritative experiment selection")
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
