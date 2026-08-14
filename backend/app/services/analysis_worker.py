"""Durable intake-analysis worker. Never claims GPU reproduction jobs."""

from __future__ import annotations

import logging
import threading

from backend.app.domain import ANALYSIS_FAILED, AnalysisJobStatus, IntakeAnalysisPhase, ReproductionIntakeState
from backend.app.llm.budget import AnalysisLeaseLostError
from backend.app.services.analysis_queue import AnalysisJobLeaseLostError

logger = logging.getLogger(__name__)


class _AnalysisHeartbeatGuard:
    def __init__(self, queue, job, lease_seconds, interval_seconds) -> None:
        self.queue = queue
        self.job = job
        self.lease_seconds = lease_seconds
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self):
        self._thread = threading.Thread(
            target=self._run,
            name=f"repropilot-analysis-heartbeat:{self.job.job_id}",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self.interval_seconds * 2))
        return False

    @property
    def lease_lost(self) -> bool:
        return self._lost.is_set()

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                self.queue.heartbeat(
                    self.job.job_id,
                    self.job.worker_id,
                    self.job.lease_token,
                    lease_seconds=self.lease_seconds,
                )
            except Exception:
                self._lost.set()
                return


class IntakeAnalysisWorker:
    def __init__(
        self, *, worker_id: str, queue, service, lease_seconds: int = 300,
        heartbeat_interval_seconds: float | None = None,
    ) -> None:
        if not worker_id.strip():
            raise ValueError("worker_id cannot be empty")
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or lease_seconds <= 0:
            raise ValueError("lease_seconds must be a positive integer")
        interval = heartbeat_interval_seconds
        if interval is None:
            interval = max(1.0, lease_seconds / 3)
        if interval <= 0 or interval >= lease_seconds:
            raise ValueError("heartbeat interval must be positive and shorter than the lease")
        self.worker_id = worker_id
        self.queue = queue
        self.service = service
        self.lease_seconds = lease_seconds
        self.heartbeat_interval_seconds = interval

    def run_once(self):
        try:
            recovered = self.queue.recover_expired()
        except Exception:
            logger.exception("analysis worker failed while recovering expired leases")
            return None
        for job in recovered:
            if job.status is AnalysisJobStatus.FAILED:
                try:
                    self.service.fail_analysis(
                        job.intake_id,
                        error_code=job.last_error_code or ANALYSIS_FAILED,
                        error_message=job.last_error or "analysis job exceeded retry budget",
                        failed_phase=IntakeAnalysisPhase.FAILED,
                    )
                except Exception:
                    logger.exception("failed to persist intake failure after lease exhaustion for %s", job.intake_id)
        try:
            job = self.queue.claim(self.worker_id, lease_seconds=self.lease_seconds)
        except Exception:
            logger.exception("analysis worker failed to claim an analysis job")
            return None
        if job is None:
            return None
        try:
            with _AnalysisHeartbeatGuard(
                self.queue, job, self.lease_seconds, self.heartbeat_interval_seconds,
            ) as heartbeat:
                def interrupt_check():
                    if heartbeat.lease_lost:
                        raise AnalysisLeaseLostError(f"lease lost for analysis job {job.job_id}")

                intake = self.service.execute_analysis_job(job, interrupt_check=interrupt_check)
                if heartbeat.lease_lost:
                    raise AnalysisLeaseLostError(f"lease lost for analysis job {job.job_id}")
                if intake.state is ReproductionIntakeState.FAILED:
                    self.queue.fail(
                        job.job_id, job.worker_id, job.lease_token,
                        intake.error_message or "analysis failed",
                        error_code=intake.error_code,
                    )
                else:
                    self.queue.succeed(job.job_id, job.worker_id, job.lease_token)
                return intake
        except (AnalysisJobLeaseLostError, AnalysisLeaseLostError):
            logger.exception("analysis worker lost lease for %s", job.job_id)
            return None
        except Exception as exc:
            logger.exception("analysis worker failed for intake %s", job.intake_id)
            try:
                self.service.fail_analysis(
                    job.intake_id,
                    error_code=getattr(exc, "code", ANALYSIS_FAILED) or ANALYSIS_FAILED,
                    error_message=str(exc) or "analysis worker crashed",
                    failed_phase=self._failed_phase(job.intake_id),
                )
            except Exception:
                logger.exception("failed to persist analysis failure for %s", job.intake_id)
            try:
                self.queue.fail(
                    job.job_id, job.worker_id, job.lease_token,
                    str(exc) or "analysis worker crashed",
                    error_code=getattr(exc, "code", ANALYSIS_FAILED) or ANALYSIS_FAILED,
                )
            except (AnalysisJobLeaseLostError, AnalysisLeaseLostError):
                logger.exception("analysis worker lost lease while failing %s", job.job_id)
            except Exception:
                logger.exception("failed to fail analysis job %s", job.job_id)
            return None

    def _failed_phase(self, intake_id: str) -> IntakeAnalysisPhase:
        try:
            return self.service.persistence.intakes.get(intake_id).current_phase
        except Exception:
            return IntakeAnalysisPhase.FAILED
