"""Lightweight durable worker that delegates scientific execution to Task 11."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import Enum

from backend.app.domain import (
    GPUSchedulingRequest, ReproductionEventType, ReproductionJob,
    ReproductionJobStatus, RunStatus,
    StepStatus,
)
from backend.app.services.gpu import GPUAllocationConflictError, GPULeaseLostError
from backend.app.services.job_queue import JobLeaseLostError
from .resource_adaptation import ResourceWaitRequired


class WorkerDisposition(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    LEASE_LOST = "lease_lost"
    WAITING_FOR_RESOURCES = "waiting_for_resources"


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
        gpu_resource_port=None,
        product_event_publisher=None,
        gpu_scheduler=None,
        result_finalizer=None,
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
        self.gpu_resource_port = gpu_resource_port
        self.product_event_publisher = product_event_publisher
        self.gpu_scheduler = gpu_scheduler
        self.result_finalizer = result_finalizer

    def run_once(self) -> WorkerResult | None:
        job = self.queue.claim(self.worker_id, lease_seconds=self.lease_seconds)
        if job is None:
            return None
        self._publish(job.job_id, ReproductionEventType.JOB_CLAIMED, {"status": "claimed"})
        self._publish_gpu_allocations(job.job_id)
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
                    warning = self._release_gpu_resources(job)
                    self._publish(job.job_id, ReproductionEventType.JOB_CANCELLED, {})
                    return WorkerResult(
                        job.job_id, WorkerDisposition.CANCELLED, run_id, warning,
                    )

                running = self.queue.mark_running(job.job_id, job.worker_id, job.lease_token)
                if running.status is ReproductionJobStatus.CANCEL_REQUESTED:
                    run_id = self._cancel(running, cancellation)
                    self._assert_lease(cancellation)
                    self.queue.cancel(job.job_id, job.worker_id, job.lease_token)
                    warning = self._release_gpu_resources(job)
                    self._publish(job.job_id, ReproductionEventType.JOB_CANCELLED, {})
                    return WorkerResult(
                        job.job_id, WorkerDisposition.CANCELLED, run_id, warning,
                    )

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
        except ResourceWaitRequired as exc:
            self._publish(job.job_id, ReproductionEventType.GPU_WAITING, {
                "step_id": exc.step_id, "reason": str(exc),
            })
            if self.gpu_resource_port is None:
                message = "GPU resource wait was requested without a worker resource port"
                self.queue.fail(job.job_id, job.worker_id, job.lease_token, message)
                self._publish(job.job_id, ReproductionEventType.JOB_FAILED, {"message": message})
                return WorkerResult(job.job_id, WorkerDisposition.FAILED, run_id, message)
            try:
                self._assert_lease(cancellation)
                self._ensure_gpu_request(job, run_id, exc)
                self.gpu_resource_port.defer(
                    job.job_id,
                    exc.step_id,
                    job.worker_id,
                    job.lease_token,
                    exc.requirement,
                )
            except (JobLeaseLostError, GPULeaseLostError) as lease_exc:
                return WorkerResult(
                    job.job_id, WorkerDisposition.LEASE_LOST, run_id, str(lease_exc),
                )
            return WorkerResult(
                job.job_id, WorkerDisposition.WAITING_FOR_RESOURCES, run_id, str(exc),
            )
        except (JobLeaseLostError, GPULeaseLostError) as exc:
            return WorkerResult(job.job_id, WorkerDisposition.LEASE_LOST, run_id, str(exc))
        except Exception as exc:
            if cancellation.lease_lost:
                return WorkerResult(job.job_id, WorkerDisposition.LEASE_LOST, run_id, type(exc).__name__)
            # Exception text from repositories, runtimes, and adapters is
            # untrusted and may contain host paths or secret-bearing command
            # details. Persist and publish a stable public summary only.
            message = f"{type(exc).__name__}: worker execution failed"
            try:
                self.queue.fail(job.job_id, job.worker_id, job.lease_token, message)
                self._release_gpu_resources(job)
                self._publish(job.job_id, ReproductionEventType.JOB_FAILED, {"message": message})
            except (JobLeaseLostError, GPULeaseLostError):
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
            if self.result_finalizer is None:
                raise RuntimeError("successful execution requires a configured result finalizer")
            self.result_finalizer.finalize(job, run)
            self.queue.succeed(job.job_id, job.worker_id, job.lease_token)
            warning = self._release_gpu_resources(job)
            self._publish(job.job_id, ReproductionEventType.JOB_SUCCEEDED, {"run_id": run.run_id})
            return WorkerResult(
                job.job_id, WorkerDisposition.SUCCEEDED, run.run_id, warning,
            )
        if run.status is RunStatus.CANCELLED:
            self._cleanup(run)
            self.queue.cancel(job.job_id, job.worker_id, job.lease_token)
            warning = self._release_gpu_resources(job)
            self._publish(job.job_id, ReproductionEventType.JOB_CANCELLED, {"run_id": run.run_id})
            return WorkerResult(
                job.job_id, WorkerDisposition.CANCELLED, run.run_id, warning,
            )
        message = run.failure.message if run.failure is not None else "reproduction run failed"
        self.queue.fail(job.job_id, job.worker_id, job.lease_token, message)
        warning = self._release_gpu_resources(job)
        self._publish(job.job_id, ReproductionEventType.JOB_FAILED, {"run_id": run.run_id, "message": message})
        if warning is not None:
            message = f"{message}; {warning}"
        return WorkerResult(job.job_id, WorkerDisposition.FAILED, run.run_id, message)

    def _publish(self, job_id, event_type, payload):
        if self.product_event_publisher is None:
            return
        try:
            self.product_event_publisher.publish(job_id, event_type, payload)
        except Exception:
            # Product event delivery must never steal or corrupt the durable job lease.
            return

    def _publish_gpu_allocations(self, job_id):
        if self.gpu_scheduler is None:
            return
        for lease in self.gpu_scheduler.active_leases_for_job(job_id):
            self._publish(job_id, ReproductionEventType.GPU_ALLOCATED, {
                "step_id": lease.step_id,
                "device_ids": list(lease.allocated_gpu_ids),
                "gpu_count": len(lease.allocated_gpu_ids),
            })

    def _ensure_gpu_request(self, job, run_id, wait):
        if self.gpu_scheduler is None:
            raise RuntimeError("GPU wait requested without a configured scheduler")
        if run_id is None:
            raise RuntimeError("GPU wait occurred before a durable run identity was assigned")
        persisted_run = self.runs.get(run_id)
        completed_steps = {
            step.step_id for step in persisted_run.steps
            if step.status is StepStatus.SUCCEEDED
        }
        for lease in self.gpu_scheduler.active_leases_for_job(job.job_id):
            if lease.step_id in completed_steps and lease.step_id != wait.step_id:
                self.gpu_scheduler.complete_step(
                    job.job_id, lease.step_id, job.worker_id,
                )
        runtime_run_id = f"{run_id}:step:{wait.step_id}"
        request = GPUSchedulingRequest(
            request_id=f"gpu-request:{job.job_id}:{wait.step_id}",
            job_id=job.job_id,
            run_id=runtime_run_id,
            step_id=wait.step_id,
            requirement=wait.requirement,
        )
        try:
            self.gpu_scheduler.submit(request)
        except GPUAllocationConflictError:
            existing = self.gpu_scheduler.get_request(request.request_id)
            if (
                existing.job_id != request.job_id
                or existing.run_id != request.run_id
                or existing.step_id != request.step_id
            ):
                raise

    def _release_gpu_resources(self, job):
        if self.gpu_resource_port is not None:
            try:
                self.gpu_resource_port.release_job(job.job_id, job.worker_id)
            except Exception as exc:
                # The durable terminal state is already committed. Reconciliation
                # releases any surviving lease without letting an old owner write.
                return f"GPU cleanup deferred to reconciliation: {exc}"
        return None

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
