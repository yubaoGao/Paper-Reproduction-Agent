"""PostgreSQL durable queue using row locks and SKIP LOCKED claims."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import exists, select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.domain import ReproductionJob, ReproductionJobStatus
from backend.app.services.job_queue import (
    InvalidJobQueueTransition,
    JobLeaseLostError,
)
from backend.app.services.persistence import PersistenceEntityNotFoundError

from .models import GPUSchedulingRequestRow, ReproductionJobRow
from .repositories import _Repository, _job_from_row, _job_values, utc_now


_TERMINAL = {
    ReproductionJobStatus.SUCCEEDED,
    ReproductionJobStatus.FAILED,
    ReproductionJobStatus.CANCELLED,
}
_OWNED = {
    ReproductionJobStatus.CLAIMED,
    ReproductionJobStatus.RUNNING,
    ReproductionJobStatus.CANCEL_REQUESTED,
}


class PostgresDurableJobQueue(_Repository):
    """Transactionally claims one FIFO job without blocking other workers."""

    def __init__(self, session_factory: sessionmaker[Session], session: Session | None = None) -> None:
        super().__init__(session_factory, session)

    def enqueue(self, job_id: str, *, now: datetime | None = None) -> ReproductionJob:
        current_time = now or utc_now()
        with self._write() as session:
            row = self._locked_row(session, job_id)
            job = self._job(row)
            if job.status is ReproductionJobStatus.QUEUED:
                return job
            if job.status is ReproductionJobStatus.CANCEL_REQUESTED:
                return job
            if job.status is not ReproductionJobStatus.READY:
                raise InvalidJobQueueTransition(f"job {job_id!r} cannot be enqueued from {job.status.value}")
            queued = _copy_job(
                job,
                status=ReproductionJobStatus.QUEUED,
                enqueued_at=current_time,
                worker_id=None,
                lease_token=None,
                claimed_at=None,
                lease_expires_at=None,
                heartbeat_at=None,
                last_error=None,
                updated_at=current_time,
            )
            self._store(row, queued)
            session.flush()
            return queued

    def claim(
        self,
        worker_id: str,
        *,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> ReproductionJob | None:
        self._validate_lease_seconds(lease_seconds)
        if not worker_id.strip():
            raise ValueError("worker_id cannot be empty")
        current_time = now or utc_now()
        with self._write() as session:
            self._recover_locked(session, current_time)
            row = session.scalar(
                select(ReproductionJobRow)
                .where(
                    ReproductionJobRow.status.in_(
                        (
                            ReproductionJobStatus.QUEUED.value,
                            ReproductionJobStatus.CANCEL_REQUESTED.value,
                        )
                    ),
                    ReproductionJobRow.lease_token.is_(None),
                )
                .order_by(
                    ReproductionJobRow.enqueued_at,
                    ReproductionJobRow.created_at,
                    ReproductionJobRow.job_id,
                )
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if row is None:
                return None
            claimed = self._claim_row(row, worker_id, lease_seconds, current_time)
            session.flush()
            return claimed

    def claim_job(self, job_id, worker_id, *, lease_seconds, now=None):
        self._validate_lease_seconds(lease_seconds)
        if not worker_id.strip():
            raise ValueError("worker_id cannot be empty")
        current_time = now or utc_now()
        with self._write() as session:
            self._recover_locked(session, current_time)
            row = session.scalar(
                select(ReproductionJobRow)
                .where(
                    ReproductionJobRow.job_id == job_id,
                    ReproductionJobRow.status.in_(
                        (ReproductionJobStatus.QUEUED.value, ReproductionJobStatus.CANCEL_REQUESTED.value)
                    ),
                    ReproductionJobRow.lease_token.is_(None),
                )
                .with_for_update(skip_locked=True)
            )
            if row is None:
                return None
            claimed = self._claim_row(row, worker_id, lease_seconds, current_time)
            session.flush()
            return claimed

    def claim_cancel_requested(self, worker_id, *, lease_seconds, now=None):
        """Cancellation is claimable without waiting for scarce execution resources."""
        self._validate_lease_seconds(lease_seconds)
        if not worker_id.strip():
            raise ValueError("worker_id cannot be empty")
        current_time = now or utc_now()
        with self._write() as session:
            self._recover_locked(session, current_time)
            row = session.scalar(
                select(ReproductionJobRow)
                .where(
                    ReproductionJobRow.status == ReproductionJobStatus.CANCEL_REQUESTED.value,
                    ReproductionJobRow.lease_token.is_(None),
                )
                .order_by(ReproductionJobRow.enqueued_at, ReproductionJobRow.job_id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if row is None:
                return None
            claimed = self._claim_row(row, worker_id, lease_seconds, current_time)
            session.flush()
            return claimed

    def claim_without_gpu_request(self, worker_id, *, lease_seconds, now=None):
        """Claim a CPU-only job without bypassing a waiting GPU request."""
        self._validate_lease_seconds(lease_seconds)
        if not worker_id.strip():
            raise ValueError("worker_id cannot be empty")
        current_time = now or utc_now()
        with self._write() as session:
            self._recover_locked(session, current_time)
            gpu_request = exists(
                select(GPUSchedulingRequestRow.request_id).where(
                    GPUSchedulingRequestRow.job_id == ReproductionJobRow.job_id,
                    GPUSchedulingRequestRow.status.in_(("waiting", "leased")),
                )
            )
            row = session.scalar(
                select(ReproductionJobRow)
                .where(
                    ReproductionJobRow.status == ReproductionJobStatus.QUEUED.value,
                    ReproductionJobRow.lease_token.is_(None),
                    ~gpu_request,
                )
                .order_by(
                    ReproductionJobRow.enqueued_at,
                    ReproductionJobRow.created_at,
                    ReproductionJobRow.job_id,
                )
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if row is None:
                return None
            claimed = self._claim_row(row, worker_id, lease_seconds, current_time)
            session.flush()
            return claimed

    def _claim_row(self, row, worker_id, lease_seconds, current_time):
        job = self._job(row)
        status = (
            ReproductionJobStatus.CANCEL_REQUESTED
            if job.status is ReproductionJobStatus.CANCEL_REQUESTED
            else ReproductionJobStatus.CLAIMED
        )
        claimed = _copy_job(
            job,
            status=status,
            worker_id=worker_id,
            lease_token=uuid.uuid4().hex,
            claimed_at=current_time,
            heartbeat_at=current_time,
            lease_expires_at=current_time + timedelta(seconds=lease_seconds),
            claim_count=job.claim_count + 1,
            updated_at=current_time,
        )
        self._store(row, claimed)
        return claimed

    def mark_running(
        self,
        job_id: str,
        worker_id: str,
        lease_token: str,
        *,
        now: datetime | None = None,
    ) -> ReproductionJob:
        current_time = now or utc_now()
        with self._write() as session:
            row = self._locked_row(session, job_id)
            job = self._require_lease(row, worker_id, lease_token, current_time)
            if job.status is ReproductionJobStatus.CANCEL_REQUESTED:
                return job
            if job.status is ReproductionJobStatus.RUNNING:
                return job
            if job.status is not ReproductionJobStatus.CLAIMED:
                raise InvalidJobQueueTransition(f"job {job_id!r} cannot start from {job.status.value}")
            running = _copy_job(job, status=ReproductionJobStatus.RUNNING, updated_at=current_time)
            self._store(row, running)
            session.flush()
            return running

    def heartbeat(
        self,
        job_id: str,
        worker_id: str,
        lease_token: str,
        *,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> ReproductionJob:
        self._validate_lease_seconds(lease_seconds)
        current_time = now or utc_now()
        with self._write() as session:
            row = self._locked_row(session, job_id)
            job = self._require_lease(row, worker_id, lease_token, current_time)
            renewed = _copy_job(
                job,
                heartbeat_at=current_time,
                lease_expires_at=current_time + timedelta(seconds=lease_seconds),
                updated_at=current_time,
            )
            self._store(row, renewed)
            session.flush()
            return renewed

    def defer(
        self,
        job_id: str,
        worker_id: str,
        lease_token: str,
        *,
        now: datetime | None = None,
    ) -> ReproductionJob:
        """Return an owned resource-waiting job to the durable FIFO queue."""
        current_time = now or utc_now()
        with self._write() as session:
            row = self._locked_row(session, job_id)
            job = self._require_lease(row, worker_id, lease_token, current_time)
            deferred = _copy_job(
                job,
                status=ReproductionJobStatus.QUEUED,
                enqueued_at=current_time,
                worker_id=None,
                lease_token=None,
                claimed_at=None,
                lease_expires_at=None,
                heartbeat_at=None,
                updated_at=current_time,
            )
            self._store(row, deferred)
            session.flush()
            return deferred

    def request_cancel(self, job_id: str, *, now: datetime | None = None) -> ReproductionJob:
        current_time = now or utc_now()
        with self._write() as session:
            row = self._locked_row(session, job_id)
            job = self._job(row)
            if job.status in _TERMINAL or job.status is ReproductionJobStatus.CANCEL_REQUESTED:
                return job
            if job.status not in {
                ReproductionJobStatus.READY,
                ReproductionJobStatus.QUEUED,
                ReproductionJobStatus.CLAIMED,
                ReproductionJobStatus.RUNNING,
            }:
                raise InvalidJobQueueTransition(
                    f"job {job_id!r} cannot request cancellation from {job.status.value}"
                )
            changes = {
                "status": ReproductionJobStatus.CANCEL_REQUESTED,
                "enqueued_at": job.enqueued_at or current_time,
                "updated_at": current_time,
            }
            cancelled = _copy_job(job, **changes)
            self._store(row, cancelled)
            session.flush()
            return cancelled

    def is_cancel_requested(self, job_id: str) -> bool:
        with self._read() as session:
            status = session.scalar(
                select(ReproductionJobRow.status).where(ReproductionJobRow.job_id == job_id)
            )
            if status is None:
                raise PersistenceEntityNotFoundError(f"unknown reproduction job {job_id!r}")
            return status == ReproductionJobStatus.CANCEL_REQUESTED.value

    def succeed(self, job_id: str, worker_id: str, lease_token: str, *, now: datetime | None = None) -> ReproductionJob:
        return self._terminal(job_id, worker_id, lease_token, ReproductionJobStatus.SUCCEEDED, None, now)

    def fail(
        self,
        job_id: str,
        worker_id: str,
        lease_token: str,
        error: str,
        *,
        now: datetime | None = None,
    ) -> ReproductionJob:
        if not error.strip():
            raise ValueError("job failure requires an error message")
        return self._terminal(job_id, worker_id, lease_token, ReproductionJobStatus.FAILED, error, now)

    def cancel(self, job_id: str, worker_id: str, lease_token: str, *, now: datetime | None = None) -> ReproductionJob:
        return self._terminal(job_id, worker_id, lease_token, ReproductionJobStatus.CANCELLED, None, now)

    def recover_expired(self, *, now: datetime | None = None) -> int:
        current_time = now or utc_now()
        with self._write() as session:
            return self._recover_locked(session, current_time)

    def _terminal(self, job_id, worker_id, lease_token, target, error, now):
        current_time = now or utc_now()
        with self._write() as session:
            row = self._locked_row(session, job_id)
            job = self._job(row)
            if job.status is target:
                return job
            if job.status in _TERMINAL:
                raise InvalidJobQueueTransition(
                    f"terminal job {job_id!r} cannot transition from {job.status.value} to {target.value}"
                )
            job = self._require_lease(row, worker_id, lease_token, current_time)
            terminal = _copy_job(
                job,
                status=target,
                heartbeat_at=current_time,
                last_error=error,
                updated_at=current_time,
            )
            self._store(row, terminal)
            session.flush()
            return terminal

    def _recover_locked(self, session: Session, now: datetime) -> int:
        rows = tuple(
            session.scalars(
                select(ReproductionJobRow)
                .where(
                    ReproductionJobRow.status.in_(tuple(status.value for status in _OWNED)),
                    ReproductionJobRow.lease_expires_at <= now,
                )
                .order_by(ReproductionJobRow.lease_expires_at, ReproductionJobRow.job_id)
                .with_for_update(skip_locked=True)
            )
        )
        for row in rows:
            job = self._job(row)
            recovered_status = (
                ReproductionJobStatus.CANCEL_REQUESTED
                if job.status is ReproductionJobStatus.CANCEL_REQUESTED
                else ReproductionJobStatus.QUEUED
            )
            recovered = _copy_job(
                job,
                status=recovered_status,
                worker_id=None,
                lease_token=None,
                claimed_at=None,
                lease_expires_at=None,
                heartbeat_at=None,
                updated_at=now,
            )
            self._store(row, recovered)
        session.flush()
        return len(rows)

    def _require_lease(self, row, worker_id: str, lease_token: str, now: datetime) -> ReproductionJob:
        job = self._job(row)
        if job.status not in _OWNED:
            raise JobLeaseLostError(f"job {job.job_id!r} is no longer actively leased")
        if job.worker_id != worker_id or job.lease_token != lease_token:
            raise JobLeaseLostError(f"worker {worker_id!r} does not own job {job.job_id!r}")
        if job.lease_expires_at is None or job.lease_expires_at <= now:
            raise JobLeaseLostError(f"lease for job {job.job_id!r} has expired")
        return job

    @staticmethod
    def _validate_lease_seconds(value: int) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError("lease_seconds must be a positive integer")

    @staticmethod
    def _locked_row(session: Session, job_id: str) -> ReproductionJobRow:
        row = session.scalar(
            select(ReproductionJobRow)
            .where(ReproductionJobRow.job_id == job_id)
            .with_for_update()
        )
        if row is None:
            raise PersistenceEntityNotFoundError(f"unknown reproduction job {job_id!r}")
        return row

    @staticmethod
    def _job(row: ReproductionJobRow) -> ReproductionJob:
        return _job_from_row(row)

    @staticmethod
    def _store(row: ReproductionJobRow, job: ReproductionJob) -> None:
        for key, value in _job_values(job).items():
            setattr(row, key, value)


def _copy_job(job: ReproductionJob, **changes) -> ReproductionJob:
    values = job.model_dump(mode="python")
    values.update(changes)
    return ReproductionJob.model_validate(values)
