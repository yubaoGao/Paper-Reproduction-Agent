"""Durable intake-analysis job models, distinct from GPU reproduction jobs."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import Field, field_validator, model_validator

from .experiment import DomainModel, NonEmptyStr, _require_aware, utc_now


class IntakeAnalysisPhase(str, Enum):
    PENDING = "pending"
    PAPER_PARSING = "paper_parsing"
    PAPER_EXTRACTING = "paper_extracting"
    GOAL_RESOLVING = "goal_resolving"
    WAITING_FOR_CLARIFICATION = "waiting_for_clarification"
    REPOSITORY_ANALYZING = "repository_analyzing"
    ALIGNING = "aligning"
    PREPARING = "preparing"
    READY_TO_RUN = "ready_to_run"
    FAILED = "failed"


class AnalysisJobStatus(str, Enum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


ANALYSIS_LLM_BUDGET_EXCEEDED = "ANALYSIS_LLM_BUDGET_EXCEEDED"
ANALYSIS_TIMEOUT = "ANALYSIS_TIMEOUT"
ANALYSIS_FAILED = "ANALYSIS_FAILED"
GOAL_NOT_FOUND = "GOAL_NOT_FOUND"
REPOSITORY_SNAPSHOT_MISSING = "REPOSITORY_SNAPSHOT_MISSING"
ANALYSIS_ARTIFACT_STORE_FAILED = "ANALYSIS_ARTIFACT_STORE_FAILED"
ANALYSIS_ENQUEUE_FAILED = "ANALYSIS_ENQUEUE_FAILED"


_TERMINAL_ANALYSIS_JOBS = {AnalysisJobStatus.SUCCEEDED, AnalysisJobStatus.FAILED}


class IntakeAnalysisJob(DomainModel):
    """One durable analysis job per intake; never shares the GPU execution queue."""

    job_id: NonEmptyStr
    intake_id: NonEmptyStr
    owner_principal: NonEmptyStr
    status: AnalysisJobStatus
    paper_artifact_uri: NonEmptyStr
    attempt_count: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=2, ge=1, le=8)
    worker_id: NonEmptyStr | None = None
    lease_token: NonEmptyStr | None = None
    claimed_at: datetime | None = None
    lease_expires_at: datetime | None = None
    heartbeat_at: datetime | None = None
    enqueued_at: datetime | None = None
    finished_at: datetime | None = None
    last_error: NonEmptyStr | None = None
    last_error_code: NonEmptyStr | None = None
    analysis_started_at: datetime | None = None
    llm_call_count: int = Field(default=0, ge=0)
    lifetime_llm_call_count: int = Field(default=0, ge=0)
    llm_call_records: tuple[dict, ...] = ()
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator(
        "created_at", "updated_at", "claimed_at", "lease_expires_at", "heartbeat_at",
        "enqueued_at", "finished_at", "analysis_started_at",
    )
    @classmethod
    def aware_times(cls, value: datetime | None, info: object) -> datetime | None:
        return _require_aware(value, info.field_name)  # type: ignore[return-value,union-attr]

    @model_validator(mode="after")
    def consistent(self):
        if self.updated_at < self.created_at:
            raise ValueError("analysis job updated_at cannot precede created_at")
        if self.status is AnalysisJobStatus.CLAIMED:
            if self.worker_id is None or self.lease_token is None or self.lease_expires_at is None:
                raise ValueError("claimed analysis job requires complete lease ownership")
        if self.status in _TERMINAL_ANALYSIS_JOBS and self.lease_token is not None:
            raise ValueError("terminal analysis job cannot retain an active lease")
        if self.attempt_count > self.max_attempts:
            raise ValueError("analysis job exceeded max_attempts")
        if self.lifetime_llm_call_count < self.llm_call_count:
            raise ValueError("lifetime LLM call count cannot be below the current phase count")
        return self
