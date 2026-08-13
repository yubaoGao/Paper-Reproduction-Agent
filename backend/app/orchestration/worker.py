"""Lightweight durable worker that delegates scientific execution to Task 11."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import Enum

from backend.app.domain import ReproductionJob, ReproductionJobStatus, RunStatus
from backend.app.services.job_queue import JobLeaseLostError


class WorkerDisposition(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    LEASE_LOST = "lease_lost"


@dataclass(frozen=True)
class WorkerResult:
    job_id: str
    disposition: WorkerDisposition
    run_id: str | None = None
    message: str | None = None


class DurableJobCancellationPort:
    """Bridges durable job cancellation and lease loss into Task 11."""

    def __init__(self, queue, job: ReproductionJob) -> None:
        self.queue = queue
        self.job_id = job.job_id
        self.worker_id = job.worker_id
        self.lease_token = job.lease_token
        self._lease_lost = threading.Event()

    def is_cancel_requested(self, run_id: str) -> bool:
        return self._lease_lost.is_set() or self.queue.is_cancel_requested(self.job_id)

    def mark_lease_lost(self) -> None:
        self._lease_lost.set()

    @property
    def lease_lost(self) -> bool:
        return self._lease_lost.is_set()


class _HeartbeatGuard:
    def __init__(self, queue, job, cancellation, lease_seconds, interval_seconds) -> None:
        self.queue = queue
        self.job = job
        self.cancellation = cancellation
        self.lease_seconds = lease_seconds
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self):
        if self.interval_seconds is not None:
            self._thread = threading.Thread(
                target=self._run,
                name=f"repropilot-heartbeat:{self.job.job_id}",
                daemon=True,
            )
            self._thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self.interval_seconds * 2))
        return False

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
                self.cancellation.mark_lease_lost()
                return


class ReproductionWorker:
    """Claims one durable job at a time and delegates execution to an executor."""

    def __init__(
        self,
        *,
        worker_id: str,
        queue,
        planning_snapshots,
        runs,
        executor_factory,
        cleanup_port=None,
        lease_seconds: int = 60,
        heartbeat_interval_seconds: float | None = None,
    ) -> None:
        if not worker_id.strip():
            raise ValueError("worker_id cannot be empty")
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or lease_seconds <= 0:
            raise ValueError("lease_seconds must be a positive integer")
        interval = heartbeat_interval_seconds
        if interval is None:
            interval = max(0.1, lease_seconds / 3)
        if interval <= 0 or interval >= lease_seconds:
            raise ValueError("heartbeat interval must be positive and shorter than the lease")
        self.worker_id = worker_id
        self.queue = queue
        self.planning_snapshots = planning_snapshots
        self.runs = runs
        self.executor_factory = executor_factory
        self.cleanup_port = cleanup_port
        self.lease_seconds = lease_seconds
        self.heartbeat_interval_seconds = interval

    def run_once(self) -> WorkerResult | None:
        job = self.queue.claim(self.worker_id, lease_seconds=self.lease_seconds)
        if job is None:
            return None
        cancellation = DurableJobCancellationPort(self.queue, job)
        run_id: str | None = None
        try:
            with _HeartbeatGuard(
                self.queue,
                job,
                cancellation,
                self.lease_seconds,
                self.heartbeat_interval_seconds,
            ):
                if job.status is ReproductionJobStatus.CANCEL_REQUESTED:
                    run_id = self._cancel(job, cancellation)
                    self._assert_lease(cancellation)
                    self.queue.cancel(job.job_id, job.worker_id, job.lease_token)
                    return WorkerResult(job.job_id, WorkerDisposition.CANCELLED, run_id)

                running = self.queue.mark_running(job.job_id, job.worker_id, job.lease_token)
                if running.status is ReproductionJobStatus.CANCEL_REQUESTED:
                    run_id = self._cancel(running, cancellation)
                    self._assert_lease(cancellation)
                    self.queue.cancel(job.job_id, job.worker_id, job.lease_token)
                    return WorkerResult(job.job_id, WorkerDisposition.CANCELLED, run_id)

                snapshot = self.planning_snapshots.get_by_job(job.job_id)
                existing = self.runs.list_by_job(job.job_id)
                if len(existing) > 1:
                    raise RuntimeError("job has multiple reproduction runs; deterministic recovery is impossible")
                executor = self.executor_factory(cancellation)
                if existing:
                    run = existing[0]
                    run_id = run.run_id
                    if run.status not in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}:
                        self._cleanup_interrupted(run)
                        run = executor.resume(snapshot.execution_plan, run.run_id)
                else:
                    run_id = self._run_id(job.job_id)
                    run = executor.execute(snapshot.execution_plan, run_id)
                self._assert_lease(cancellation)
                return self._finish(job, run)
        except JobLeaseLostError as exc:
            return WorkerResult(job.job_id, WorkerDisposition.LEASE_LOST, run_id, str(exc))
        except Exception as exc:
            if cancellation.lease_lost:
                return WorkerResult(job.job_id, WorkerDisposition.LEASE_LOST, run_id, type(exc).__name__)
            message = f"{type(exc).__name__}: {exc}"
            try:
                self.queue.fail(job.job_id, job.worker_id, job.lease_token, message)
            except JobLeaseLostError:
                return WorkerResult(job.job_id, WorkerDisposition.LEASE_LOST, run_id, message)
            return WorkerResult(job.job_id, WorkerDisposition.FAILED, run_id, message)

    def run_until_empty(self, *, max_jobs: int) -> tuple[WorkerResult, ...]:
        if isinstance(max_jobs, bool) or not isinstance(max_jobs, int) or max_jobs <= 0:
            raise ValueError("max_jobs must be a positive integer")
        results = []
        for _ in range(max_jobs):
            result = self.run_once()
            if result is None:
                break
            results.append(result)
        return tuple(results)

    def _finish(self, job, run) -> WorkerResult:
        if run.status is RunStatus.SUCCEEDED:
            self.queue.succeed(job.job_id, job.worker_id, job.lease_token)
            return WorkerResult(job.job_id, WorkerDisposition.SUCCEEDED, run.run_id)
        if run.status is RunStatus.CANCELLED:
            self._cleanup(run)
            self.queue.cancel(job.job_id, job.worker_id, job.lease_token)
            return WorkerResult(job.job_id, WorkerDisposition.CANCELLED, run.run_id)
        message = run.failure.message if run.failure is not None else "reproduction run failed"
        self.queue.fail(job.job_id, job.worker_id, job.lease_token, message)
        return WorkerResult(job.job_id, WorkerDisposition.FAILED, run.run_id, message)

    def _cancel(self, job, cancellation) -> str | None:
        existing = self.runs.list_by_job(job.job_id)
        if len(existing) > 1:
            raise RuntimeError("job has multiple reproduction runs; cancellation is ambiguous")
        if not existing:
            return None
        run = existing[0]
        if run.status not in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}:
            snapshot = self.planning_snapshots.get_by_job(job.job_id)
            run = self.executor_factory(cancellation).resume(snapshot.execution_plan, run.run_id)
        self._cleanup(run)
        return run.run_id

    def _cleanup(self, run) -> None:
        if self.cleanup_port is not None:
            for step in run.steps:
                runtime_run_id = f"{run.run_id}:step:{step.step_id}"
                try:
                    self.cleanup_port.cleanup(runtime_run_id)
                except KeyError:
                    # Already-cleaned or never-started steps have no registered resources.
                    continue

    def _cleanup_interrupted(self, run) -> None:
        active = {"preparing", "running", "validating", "patching", "retrying"}
        if self.cleanup_port is None:
            return
        for step in run.steps:
            if step.status.value not in active:
                continue
            try:
                self.cleanup_port.cleanup(f"{run.run_id}:step:{step.step_id}")
            except KeyError:
                continue

    @staticmethod
    def _assert_lease(cancellation: DurableJobCancellationPort) -> None:
        if cancellation.lease_lost:
            raise JobLeaseLostError(f"lease for job {cancellation.job_id!r} was lost during execution")

    @staticmethod
    def _run_id(job_id: str) -> str:
        return f"reproduction-job:{job_id}"
