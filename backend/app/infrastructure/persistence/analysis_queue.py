"""PostgreSQL intake-analysis queue. Independent from the GPU reproduction queue."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.domain import ANALYSIS_FAILED, AnalysisJobStatus, IntakeAnalysisJob
from backend.app.llm.budget import AnalysisLLMBudgetExceeded
from backend.app.services.analysis_queue import (
    AnalysisJobLeaseLostError,
    InvalidAnalysisQueueTransition,
)
from backend.app.services.persistence import PersistenceEntityNotFoundError

from .models import IntakeAnalysisJobRow
from .repositories import _Repository, utc_now
from .serialization import deserialize_domain, serialize_domain


class PostgresIntakeAnalysisQueue(_Repository):
    def __init__(self, session_factory: sessionmaker[Session], session: Session | None = None) -> None:
        super().__init__(session_factory, session)

    def enqueue(self, job: IntakeAnalysisJob, *, now: datetime | None = None) -> IntakeAnalysisJob:
        current = now or utc_now()
        with self._write() as session:
            row = session.scalar(
                select(IntakeAnalysisJobRow)
                .where(IntakeAnalysisJobRow.intake_id == job.intake_id)
                .with_for_update()
            )
            if row is None:
                queued = job.model_copy(update={
                    "status": AnalysisJobStatus.QUEUED,
                    "enqueued_at": current,
                    "worker_id": None,
                    "lease_token": None,
                    "claimed_at": None,
                    "lease_expires_at": None,
                    "heartbeat_at": None,
                    "finished_at": None,
                    "updated_at": current,
                })
                session.add(IntakeAnalysisJobRow(job_id=queued.job_id, **_analysis_values(queued)))
                session.flush()
                return queued
            existing = _job_from_row(row)
            if existing.status in {AnalysisJobStatus.QUEUED, AnalysisJobStatus.CLAIMED}:
                return existing
            queued = existing.model_copy(update={
                "status": AnalysisJobStatus.QUEUED,
                "enqueued_at": current,
                "worker_id": None,
                "lease_token": None,
                "claimed_at": None,
                "lease_expires_at": None,
                "heartbeat_at": None,
                "finished_at": None,
                "last_error": None,
                "last_error_code": None,
                "updated_at": current,
            })
            _store(row, queued)
            session.flush()
            return queued

    def enqueue_for_clarification(self, job: IntakeAnalysisJob, *, now: datetime | None = None) -> IntakeAnalysisJob:
        current = now or utc_now()
        with self._write() as session:
            row = session.scalar(
                select(IntakeAnalysisJobRow)
                .where(IntakeAnalysisJobRow.intake_id == job.intake_id)
                .with_for_update()
            )
            if row is not None:
                existing = _job_from_row(row)
                if existing.status in {AnalysisJobStatus.QUEUED, AnalysisJobStatus.CLAIMED}:
                    return existing
                queued = existing.model_copy(update={
                    "status": AnalysisJobStatus.QUEUED,
                    "attempt_count": 0,
                    "analysis_started_at": None,
                    "llm_call_count": 0,
                    "enqueued_at": current,
                    "worker_id": None,
                    "lease_token": None,
                    "claimed_at": None,
                    "lease_expires_at": None,
                    "heartbeat_at": None,
                    "finished_at": None,
                    "last_error": None,
                    "last_error_code": None,
                    "updated_at": current,
                })
                _store(row, queued)
                session.flush()
                return queued
            queued = job.model_copy(update={
                "status": AnalysisJobStatus.QUEUED,
                "attempt_count": 0,
                "analysis_started_at": None,
                "llm_call_count": 0,
                "enqueued_at": current,
                "worker_id": None,
                "lease_token": None,
                "claimed_at": None,
                "lease_expires_at": None,
                "heartbeat_at": None,
                "finished_at": None,
                "updated_at": current,
            })
            session.add(IntakeAnalysisJobRow(job_id=queued.job_id, **_analysis_values(queued)))
            session.flush()
            return queued

    def claim(self, worker_id: str, *, lease_seconds: int, now: datetime | None = None) -> IntakeAnalysisJob | None:
        if not worker_id.strip():
            raise ValueError("worker_id cannot be empty")
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or lease_seconds <= 0:
            raise ValueError("lease_seconds must be a positive integer")
        current = now or utc_now()
        with self._write() as session:
            self._recover_locked(session, current)
            row = session.scalar(
                select(IntakeAnalysisJobRow)
                .where(
                    IntakeAnalysisJobRow.status == AnalysisJobStatus.QUEUED.value,
                    IntakeAnalysisJobRow.lease_token.is_(None),
                )
                .order_by(
                    IntakeAnalysisJobRow.enqueued_at,
                    IntakeAnalysisJobRow.created_at,
                    IntakeAnalysisJobRow.job_id,
                )
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if row is None:
                return None
            job = _job_from_row(row)
            claimed = job.model_copy(update={
                "status": AnalysisJobStatus.CLAIMED,
                "worker_id": worker_id,
                "lease_token": uuid.uuid4().hex,
                "claimed_at": current,
                "heartbeat_at": current,
                "lease_expires_at": current + timedelta(seconds=lease_seconds),
                "attempt_count": job.attempt_count + 1,
                "updated_at": current,
            })
            _store(row, claimed)
            session.flush()
            return claimed

    def heartbeat(self, job_id, worker_id, lease_token, *, lease_seconds, now=None):
        current = now or utc_now()
        with self._write() as session:
            row = self._locked_row(session, job_id)
            job = self._require_lease(row, worker_id, lease_token, current)
            renewed = job.model_copy(update={
                "heartbeat_at": current,
                "lease_expires_at": current + timedelta(seconds=lease_seconds),
                "updated_at": current,
            })
            _store(row, renewed)
            session.flush()
            return renewed

    def mark_analysis_started(self, job_id, worker_id, lease_token, *, now=None):
        current = now or utc_now()
        with self._write() as session:
            row = self._locked_row(session, job_id)
            job = self._require_lease(row, worker_id, lease_token, current)
            if job.analysis_started_at is not None:
                return job
            started = job.model_copy(update={"analysis_started_at": current, "updated_at": current})
            _store(row, started)
            session.flush()
            return started

    def record_llm_http_attempt(self, job_id, worker_id, lease_token, *, max_phase_calls, now=None):
        current = now or utc_now()
        with self._write() as session:
            row = self._locked_row(session, job_id)
            job = self._require_lease(row, worker_id, lease_token, current)
            if job.llm_call_count >= max_phase_calls:
                raise AnalysisLLMBudgetExceeded(
                    f"intake analysis exceeded {max_phase_calls} LLM calls"
                )
            updated = job.model_copy(update={
                "llm_call_count": job.llm_call_count + 1,
                "lifetime_llm_call_count": job.lifetime_llm_call_count + 1,
                "updated_at": current,
            })
            _store(row, updated)
            session.flush()
            return updated

    def succeed(self, job_id, worker_id, lease_token, *, now=None, llm_call_count=None, llm_call_records=()):
        return self._terminal(
            job_id, worker_id, lease_token, AnalysisJobStatus.SUCCEEDED, None, None,
            now, llm_call_count, llm_call_records,
        )

    def fail(
        self, job_id, worker_id, lease_token, error, *, error_code=None, now=None,
        llm_call_count=None, llm_call_records=(),
    ):
        if not error.strip():
            raise ValueError("analysis failure requires an error message")
        return self._terminal(
            job_id, worker_id, lease_token, AnalysisJobStatus.FAILED, error, error_code,
            now, llm_call_count, llm_call_records,
        )

    def get_by_intake(self, intake_id: str) -> IntakeAnalysisJob | None:
        with self._read() as session:
            row = session.scalar(
                select(IntakeAnalysisJobRow).where(IntakeAnalysisJobRow.intake_id == intake_id)
            )
            return None if row is None else _job_from_row(row)

    def recover_expired(self, *, now: datetime | None = None) -> tuple[IntakeAnalysisJob, ...]:
        current = now or utc_now()
        with self._write() as session:
            return self._recover_locked(session, current)

    def _terminal(self, job_id, worker_id, lease_token, status, error, error_code, now, llm_call_count, llm_call_records):
        current = now or utc_now()
        with self._write() as session:
            row = self._locked_row(session, job_id)
            job = _job_from_row(row)
            if job.status is status:
                return job
            if job.status in {AnalysisJobStatus.SUCCEEDED, AnalysisJobStatus.FAILED}:
                raise InvalidAnalysisQueueTransition(
                    f"terminal analysis job {job_id!r} cannot transition from {job.status.value}"
                )
            job = self._require_lease(row, worker_id, lease_token, current)
            phase = job.llm_call_count if llm_call_count is None else max(job.llm_call_count, llm_call_count)
            terminal = job.model_copy(update={
                "status": status,
                "worker_id": None,
                "lease_token": None,
                "lease_expires_at": None,
                "heartbeat_at": current,
                "finished_at": current,
                "last_error": error,
                "last_error_code": error_code,
                "llm_call_count": phase,
                "lifetime_llm_call_count": max(job.lifetime_llm_call_count, phase),
                "llm_call_records": tuple(llm_call_records) if llm_call_records else job.llm_call_records,
                "updated_at": current,
            })
            _store(row, terminal)
            session.flush()
            return terminal

    def _recover_locked(self, session: Session, now: datetime) -> tuple[IntakeAnalysisJob, ...]:
        rows = tuple(
            session.scalars(
                select(IntakeAnalysisJobRow)
                .where(
                    IntakeAnalysisJobRow.status == AnalysisJobStatus.CLAIMED.value,
                    IntakeAnalysisJobRow.lease_expires_at <= now,
                )
                .order_by(IntakeAnalysisJobRow.lease_expires_at, IntakeAnalysisJobRow.job_id)
                .with_for_update(skip_locked=True)
            )
        )
        recovered = []
        for row in rows:
            job = _job_from_row(row)
            if job.attempt_count >= job.max_attempts:
                failed = job.model_copy(update={
                    "status": AnalysisJobStatus.FAILED,
                    "worker_id": None,
                    "lease_token": None,
                    "lease_expires_at": None,
                    "heartbeat_at": None,
                    "finished_at": now,
                    "last_error": "analysis job exceeded retry budget after lease expiry",
                    "last_error_code": ANALYSIS_FAILED,
                    "updated_at": now,
                })
                _store(row, failed)
                recovered.append(failed)
                continue
            queued = job.model_copy(update={
                "status": AnalysisJobStatus.QUEUED,
                "worker_id": None,
                "lease_token": None,
                "claimed_at": None,
                "lease_expires_at": None,
                "heartbeat_at": None,
                "enqueued_at": now,
                "updated_at": now,
            })
            _store(row, queued)
            recovered.append(queued)
        session.flush()
        return tuple(recovered)

    def _require_lease(self, row, worker_id, lease_token, now) -> IntakeAnalysisJob:
        job = _job_from_row(row)
        if job.status is not AnalysisJobStatus.CLAIMED:
            raise AnalysisJobLeaseLostError(f"analysis job {job.job_id!r} is no longer actively leased")
        if job.worker_id != worker_id or job.lease_token != lease_token:
            raise AnalysisJobLeaseLostError(f"worker {worker_id!r} does not own analysis job {job.job_id!r}")
        if job.lease_expires_at is None or job.lease_expires_at <= now:
            raise AnalysisJobLeaseLostError(f"lease for analysis job {job.job_id!r} has expired")
        return job

    @staticmethod
    def _locked_row(session: Session, job_id: str) -> IntakeAnalysisJobRow:
        row = session.scalar(
            select(IntakeAnalysisJobRow).where(IntakeAnalysisJobRow.job_id == job_id).with_for_update()
        )
        if row is None:
            raise PersistenceEntityNotFoundError(f"unknown analysis job {job_id!r}")
        return row


def _analysis_values(job: IntakeAnalysisJob) -> dict:
    return {
        "intake_id": job.intake_id,
        "owner_principal": job.owner_principal,
        "status": job.status.value,
        "paper_artifact_uri": job.paper_artifact_uri,
        "attempt_count": job.attempt_count,
        "max_attempts": job.max_attempts,
        "worker_id": job.worker_id,
        "lease_token": job.lease_token,
        "claimed_at": job.claimed_at,
        "lease_expires_at": job.lease_expires_at,
        "heartbeat_at": job.heartbeat_at,
        "enqueued_at": job.enqueued_at,
        "finished_at": job.finished_at,
        "last_error": job.last_error,
        "last_error_code": job.last_error_code,
        "analysis_started_at": job.analysis_started_at,
        "llm_call_count": job.llm_call_count,
        "lifetime_llm_call_count": job.lifetime_llm_call_count,
        "job_json": serialize_domain(job),
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


def _store(row: IntakeAnalysisJobRow, job: IntakeAnalysisJob) -> None:
    for key, value in _analysis_values(job).items():
        setattr(row, key, value)


def _job_from_row(row: IntakeAnalysisJobRow) -> IntakeAnalysisJob:
    payload = dict(row.job_json)
    payload.update({
        "status": row.status,
        "attempt_count": row.attempt_count,
        "worker_id": row.worker_id,
        "lease_token": row.lease_token,
        "claimed_at": row.claimed_at,
        "lease_expires_at": row.lease_expires_at,
        "heartbeat_at": row.heartbeat_at,
        "enqueued_at": row.enqueued_at,
        "finished_at": row.finished_at,
        "last_error": row.last_error,
        "last_error_code": row.last_error_code,
        "analysis_started_at": row.analysis_started_at,
        "llm_call_count": row.llm_call_count,
        "lifetime_llm_call_count": row.lifetime_llm_call_count,
        "updated_at": row.updated_at,
    })
    return deserialize_domain(payload, IntakeAnalysisJob)
