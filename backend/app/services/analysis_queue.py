"""SQL-free durable intake-analysis queue, separate from GPU reproduction jobs."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from backend.app.domain import IntakeAnalysisJob


class AnalysisJobConflictError(RuntimeError):
    pass


class AnalysisJobLeaseLostError(AnalysisJobConflictError):
    pass


class InvalidAnalysisQueueTransition(ValueError):
    pass


@runtime_checkable
class IntakeAnalysisQueue(Protocol):
    def enqueue(self, job: IntakeAnalysisJob, *, now: datetime | None = None) -> IntakeAnalysisJob: ...
    def enqueue_for_clarification(
        self, job: IntakeAnalysisJob, *, now: datetime | None = None,
    ) -> IntakeAnalysisJob: ...
    def claim(
        self, worker_id: str, *, lease_seconds: int, now: datetime | None = None,
    ) -> IntakeAnalysisJob | None: ...
    def heartbeat(
        self, job_id: str, worker_id: str, lease_token: str, *,
        lease_seconds: int, now: datetime | None = None,
    ) -> IntakeAnalysisJob: ...
    def mark_analysis_started(
        self, job_id: str, worker_id: str, lease_token: str, *,
        now: datetime | None = None,
    ) -> IntakeAnalysisJob: ...
    def record_llm_http_attempt(
        self, job_id: str, worker_id: str, lease_token: str, *,
        max_phase_calls: int, now: datetime | None = None,
    ) -> IntakeAnalysisJob: ...
    def succeed(
        self, job_id: str, worker_id: str, lease_token: str, *,
        now: datetime | None = None, llm_call_count: int | None = None,
        llm_call_records: tuple[dict, ...] = (),
    ) -> IntakeAnalysisJob: ...
    def fail(
        self, job_id: str, worker_id: str, lease_token: str, error: str, *,
        error_code: str | None = None, now: datetime | None = None,
        llm_call_count: int | None = None, llm_call_records: tuple[dict, ...] = (),
    ) -> IntakeAnalysisJob: ...
    def get_by_intake(self, intake_id: str) -> IntakeAnalysisJob | None: ...
    def recover_expired(self, *, now: datetime | None = None) -> tuple[IntakeAnalysisJob, ...]: ...
