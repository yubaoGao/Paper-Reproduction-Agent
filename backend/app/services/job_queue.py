"""SQL-free durable job queue and worker execution contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from backend.app.domain import ReproductionJob, ReproductionRun


class JobLeaseConflictError(RuntimeError):
    pass


class JobLeaseLostError(JobLeaseConflictError):
    pass


class InvalidJobQueueTransition(ValueError):
    pass


@runtime_checkable
class DurableJobQueue(Protocol):
    def enqueue(self, job_id: str, *, now: datetime | None = None) -> ReproductionJob: ...
    def claim(
        self,
        worker_id: str,
        *,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> ReproductionJob | None: ...
    def claim_job(
        self,
        job_id: str,
        worker_id: str,
        *,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> ReproductionJob | None: ...
    def mark_running(
        self,
        job_id: str,
        worker_id: str,
        lease_token: str,
        *,
        now: datetime | None = None,
    ) -> ReproductionJob: ...
    def heartbeat(
        self,
        job_id: str,
        worker_id: str,
        lease_token: str,
        *,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> ReproductionJob: ...
    def defer(
        self,
        job_id: str,
        worker_id: str,
        lease_token: str,
        *,
        now: datetime | None = None,
    ) -> ReproductionJob: ...
    def request_cancel(self, job_id: str, *, now: datetime | None = None) -> ReproductionJob: ...
    def is_cancel_requested(self, job_id: str) -> bool: ...
    def succeed(self, job_id: str, worker_id: str, lease_token: str, *, now: datetime | None = None) -> ReproductionJob: ...
    def fail(self, job_id: str, worker_id: str, lease_token: str, error: str, *, now: datetime | None = None) -> ReproductionJob: ...
    def cancel(self, job_id: str, worker_id: str, lease_token: str, *, now: datetime | None = None) -> ReproductionJob: ...
    def recover_expired(self, *, now: datetime | None = None) -> int: ...


@runtime_checkable
class ReproductionExecutor(Protocol):
    def execute(self, plan, run_id: str) -> ReproductionRun: ...
    def resume(self, plan, run_id: str) -> ReproductionRun: ...


@runtime_checkable
class ReproductionExecutorFactory(Protocol):
    def __call__(self, cancellation_port) -> ReproductionExecutor: ...
