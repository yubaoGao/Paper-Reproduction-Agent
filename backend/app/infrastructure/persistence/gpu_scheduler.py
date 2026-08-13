"""Transactional PostgreSQL GPU scheduler with bounded backfilling."""

from __future__ import annotations

import uuid
import math
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.domain import (
    GPUDevice, GPUDeviceState, GPULease, GPURequirement, GPURequestStatus,
    GPUSchedulingRequest, ReproductionJobStatus,
)
from backend.app.services.gpu import (
    GPUAllocationConflictError, GPUInventoryUnavailableError, GPULeaseLostError,
)
from backend.app.services.persistence import PersistenceEntityNotFoundError

from .models import GPUDeviceRow, GPULeaseRow, GPUSchedulingRequestRow, ReproductionJobRow
from .serialization import deserialize_domain, serialize_domain


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PostgresGPUScheduler:
    """Locks request and device rows so an active GPU has exactly one owner."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        aging_seconds: int = 300,
        max_backfills: int = 3,
        inventory_provider=None,
        require_live_inventory: bool = True,
        memory_safety_margin_mb: int = 1024,
        memory_safety_margin_ratio: float = 0.05,
        external_usage_tolerance_mb: int = 512,
    ) -> None:
        if aging_seconds < 1 or max_backfills < 1:
            raise ValueError("GPU scheduler fairness bounds must be positive")
        if memory_safety_margin_mb < 0 or not 0 <= memory_safety_margin_ratio < 1:
            raise ValueError("GPU memory safety margins are invalid")
        if external_usage_tolerance_mb < 0:
            raise ValueError("external GPU usage tolerance cannot be negative")
        self._session_factory = session_factory
        self.aging_seconds = aging_seconds
        self.max_backfills = max_backfills
        self.inventory_provider = inventory_provider
        self.require_live_inventory = require_live_inventory
        self.memory_safety_margin_mb = memory_safety_margin_mb
        self.memory_safety_margin_ratio = memory_safety_margin_ratio
        self.external_usage_tolerance_mb = external_usage_tolerance_mb

    def refresh_inventory(self, devices: tuple[GPUDevice, ...]) -> None:
        if len({item.gpu_id for item in devices}) != len(devices):
            raise ValueError("GPU inventory contains duplicate device IDs")
        with self._session_factory.begin() as session:
            self._refresh_inventory_rows(session, devices)

    def inventory(self) -> tuple[GPUDevice, ...]:
        with self._session_factory() as session:
            rows = session.scalars(select(GPUDeviceRow).order_by(GPUDeviceRow.gpu_id))
            return tuple(self._device_from_row(row) for row in rows)

    def submit(self, request: GPUSchedulingRequest) -> None:
        if request.requirement.minimum_gpu_count < 1:
            raise ValueError("GPU scheduling request requires at least one GPU")
        try:
            with self._session_factory.begin() as session:
                session.add(self._request_row(request))
                session.flush()
        except IntegrityError as exc:
            raise GPUAllocationConflictError(
                f"GPU scheduling request {request.request_id!r} already exists"
            ) from exc

    def get_request(self, request_id: str) -> GPUSchedulingRequest:
        with self._session_factory() as session:
            row = session.get(GPUSchedulingRequestRow, request_id)
            if row is None:
                raise PersistenceEntityNotFoundError(f"unknown GPU request {request_id!r}")
            return self._request_from_row(row)

    def get_request_by_lease(self, lease_token: str) -> GPUSchedulingRequest:
        with self._session_factory() as session:
            row = session.scalar(
                select(GPUSchedulingRequestRow).where(
                    GPUSchedulingRequestRow.active_lease_token == lease_token
                )
            )
            if row is None:
                raise PersistenceEntityNotFoundError(
                    f"unknown active GPU request for lease {lease_token!r}"
                )
            return self._request_from_row(row)

    def allocate_next(
        self,
        worker_id: str,
        *,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> GPULease | None:
        if not worker_id.strip() or lease_seconds < 1:
            raise ValueError("worker ID and positive GPU lease duration are required")
        moment = now or utc_now()
        self.reconcile(now=moment)
        live_inventory = self._read_live_inventory()
        with self._session_factory.begin() as session:
            if live_inventory is not None:
                self._refresh_inventory_rows(session, live_inventory)
            self._recover_expired(session, moment)
            requests = tuple(
                session.scalars(
                    select(GPUSchedulingRequestRow)
                    .join(ReproductionJobRow, ReproductionJobRow.job_id == GPUSchedulingRequestRow.job_id)
                    .where(GPUSchedulingRequestRow.status == GPURequestStatus.WAITING.value)
                    .where(ReproductionJobRow.status == ReproductionJobStatus.QUEUED.value)
                    .order_by(GPUSchedulingRequestRow.queued_at, GPUSchedulingRequestRow.request_id)
                    .with_for_update(skip_locked=True)
                )
            )
            if not requests:
                return None
            devices = tuple(
                session.scalars(
                    select(GPUDeviceRow)
                    .where(
                        GPUDeviceRow.state == GPUDeviceState.AVAILABLE.value,
                        GPUDeviceRow.active_lease_token.is_(None),
                    )
                    .order_by(GPUDeviceRow.gpu_id)
                    .with_for_update(skip_locked=True)
                )
            )
            chosen = None
            chosen_devices: tuple[GPUDeviceRow, ...] = ()
            oldest = requests[0]
            reserved = (
                oldest.skip_count >= self.max_backfills
                or (moment - oldest.queued_at).total_seconds() >= self.aging_seconds
            )
            candidates = requests[:1] if reserved else requests
            for request_row in candidates:
                matching = tuple(
                    device for device in devices
                    if self._safe_for_allocation(device, request_row.estimated_memory_mb)
                )
                count = (
                    request_row.preferred_gpu_count
                    if len(matching) >= request_row.preferred_gpu_count
                    else request_row.minimum_gpu_count
                )
                if len(matching) >= count:
                    chosen = request_row
                    chosen_devices = matching[:count]
                    break
            if chosen is None:
                return None
            for skipped in requests:
                if skipped is chosen:
                    break
                skipped.skip_count += 1
                self._sync_request_json(skipped)
            token = uuid.uuid4().hex
            expiry = moment + timedelta(seconds=lease_seconds)
            lease = GPULease(
                lease_token=token,
                job_id=chosen.job_id,
                run_id=chosen.run_id,
                step_id=chosen.step_id,
                worker_id=worker_id,
                allocated_gpu_ids=tuple(item.gpu_id for item in chosen_devices),
                created_at=moment,
                expires_at=expiry,
                heartbeat_at=moment,
            )
            chosen.status = GPURequestStatus.LEASED.value
            chosen.active_lease_token = token
            self._sync_request_json(chosen)
            for device in chosen_devices:
                device.state = GPUDeviceState.LEASED.value
                device.active_lease_token = token
            session.add(
                GPULeaseRow(
                    lease_token=token,
                    request_id=chosen.request_id,
                    job_id=chosen.job_id,
                    run_id=chosen.run_id,
                    step_id=chosen.step_id,
                    worker_id=worker_id,
                    allocated_gpu_ids_json=list(lease.allocated_gpu_ids),
                    status="active",
                    created_at=moment,
                    expires_at=expiry,
                    heartbeat_at=moment,
                )
            )
            session.flush()
            return lease

    def heartbeat(
        self,
        lease_token: str,
        worker_id: str,
        *,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> GPULease:
        if lease_seconds < 1:
            raise ValueError("GPU lease duration must be positive")
        moment = now or utc_now()
        with self._session_factory.begin() as session:
            row = self._active_owned_lease(session, lease_token, worker_id, moment)
            row.heartbeat_at = moment
            row.expires_at = moment + timedelta(seconds=lease_seconds)
            return self._lease_from_row(row)

    def release(
        self,
        lease_token: str,
        worker_id: str,
        *,
        now: datetime | None = None,
    ) -> None:
        self._finish_lease(
            lease_token, worker_id, GPURequestStatus.COMPLETED, now=now,
        )

    def requeue(
        self,
        lease_token: str,
        worker_id: str,
        requirement: GPURequirement,
        *,
        now: datetime | None = None,
    ) -> None:
        self._finish_lease(
            lease_token, worker_id, GPURequestStatus.WAITING,
            requirement=requirement, now=now,
        )

    def _finish_lease(self, lease_token, worker_id, target, *, requirement=None, now=None):
        moment = now or utc_now()
        with self._session_factory.begin() as session:
            row = session.scalar(
                select(GPULeaseRow)
                .where(GPULeaseRow.lease_token == lease_token)
                .with_for_update()
            )
            if row is None:
                raise PersistenceEntityNotFoundError(f"unknown GPU lease {lease_token!r}")
            if row.worker_id != worker_id:
                raise GPULeaseLostError("GPU lease is owned by another worker")
            if row.status != "active":
                if target is GPURequestStatus.COMPLETED and row.status == "released":
                    return
                raise GPULeaseLostError("GPU lease is no longer active")
            self._clear_devices(session, row.lease_token)
            request_row = session.get(GPUSchedulingRequestRow, row.request_id)
            if request_row is not None:
                request_row.status = target.value
                request_row.active_lease_token = None
                if target is GPURequestStatus.WAITING:
                    request_row.queued_at = moment
                    request_row.skip_count = 0
                if requirement is not None:
                    request_row.minimum_gpu_count = requirement.minimum_gpu_count
                    request_row.preferred_gpu_count = requirement.preferred_gpu_count
                    request_row.estimated_memory_mb = requirement.estimated_memory_mb
                    payload = self._request_from_row(request_row).model_copy(
                        update={"requirement": requirement}
                    )
                    request_row.request_json = serialize_domain(payload)
                self._sync_request_json(request_row)
            row.status = "released" if target is GPURequestStatus.COMPLETED else "requeued"
            row.released_at = moment

    def recover_expired(self, *, now: datetime | None = None) -> int:
        return self.reconcile(now=now)

    def _recover_expired(self, session: Session, moment: datetime) -> int:
        rows = tuple(
            session.scalars(
                select(GPULeaseRow)
                .join(ReproductionJobRow, ReproductionJobRow.job_id == GPULeaseRow.job_id)
                .where(GPULeaseRow.status == "active", GPULeaseRow.expires_at <= moment)
                .order_by(GPULeaseRow.expires_at, GPULeaseRow.lease_token)
                .with_for_update(skip_locked=True)
            )
        )
        for row in rows:
            self._clear_devices(session, row.lease_token)
            request_row = session.get(GPUSchedulingRequestRow, row.request_id)
            if request_row is not None:
                request_row.status = GPURequestStatus.WAITING.value
                request_row.active_lease_token = None
                request_row.queued_at = moment
                self._sync_request_json(request_row)
            row.status = "expired"
            row.released_at = moment
        return len(rows)

    def reconcile(self, *, now: datetime | None = None) -> int:
        """Release expired and terminal GPU leases.

        A job remains QUEUED for a short, legitimate interval between GPU
        allocation and the job-claim CAS. Treating QUEUED as abandoned lets a
        second scheduler release that newly created lease. Deferral explicitly
        requeues its lease, and a worker crash is recovered by lease expiry.
        """
        moment = now or utc_now()
        terminal = {
            ReproductionJobStatus.SUCCEEDED.value,
            ReproductionJobStatus.FAILED.value,
            ReproductionJobStatus.CANCELLED.value,
        }
        with self._session_factory.begin() as session:
            rows = tuple(
                session.scalars(
                    select(GPULeaseRow)
                    .join(ReproductionJobRow, ReproductionJobRow.job_id == GPULeaseRow.job_id)
                    .where(
                        GPULeaseRow.status == "active",
                        or_(
                            GPULeaseRow.expires_at <= moment,
                            ReproductionJobRow.status.in_(terminal),
                        ),
                    )
                    .order_by(GPULeaseRow.expires_at, GPULeaseRow.lease_token)
                    .with_for_update(skip_locked=True)
                )
            )
            for row in rows:
                job_status = session.scalar(
                    select(ReproductionJobRow.status).where(
                        ReproductionJobRow.job_id == row.job_id
                    )
                )
                self._clear_devices(session, row.lease_token)
                request_row = session.get(GPUSchedulingRequestRow, row.request_id)
                is_terminal = job_status in terminal
                if request_row is not None:
                    request_row.status = (
                        GPURequestStatus.COMPLETED.value
                        if is_terminal else GPURequestStatus.WAITING.value
                    )
                    request_row.active_lease_token = None
                    if not is_terminal:
                        request_row.queued_at = moment
                    self._sync_request_json(request_row)
                row.status = "released" if is_terminal else "expired"
                row.released_at = moment
            return len(rows)

    def _read_live_inventory(self):
        if self.inventory_provider is None:
            if self.require_live_inventory:
                raise GPUInventoryUnavailableError(
                    "live NVIDIA inventory is required before GPU allocation"
                )
            return None
        try:
            devices = tuple(self.inventory_provider.discover())
        except Exception as exc:
            raise GPUInventoryUnavailableError(
                "live NVIDIA inventory refresh failed; stale database inventory was not used"
            ) from exc
        if len({item.gpu_id for item in devices}) != len(devices):
            raise GPUInventoryUnavailableError("live NVIDIA inventory contains duplicate IDs")
        return devices

    def _safe_for_allocation(self, device, estimated_memory_mb):
        externally_used = max(0, device.total_memory_mb - device.available_memory_mb)
        if externally_used > self.external_usage_tolerance_mb:
            return False
        safety_margin = max(
            self.memory_safety_margin_mb,
            math.ceil(device.total_memory_mb * self.memory_safety_margin_ratio),
        )
        usable = max(0, device.available_memory_mb - safety_margin)
        return usable > 0 and (
            estimated_memory_mb is None or estimated_memory_mb <= usable
        )

    def _refresh_inventory_rows(self, session, devices):
        observed = {item.gpu_id for item in devices}
        existing = tuple(session.scalars(select(GPUDeviceRow).with_for_update()))
        by_id = {item.gpu_id: item for item in existing}
        for device in devices:
            row = by_id.get(device.gpu_id)
            if row is None:
                session.add(self._device_row(device))
                continue
            row.total_memory_mb = device.total_memory_mb
            row.available_memory_mb = device.available_memory_mb
            row.model_name = device.model_name
            row.evidence_json = list(device.evidence)
            row.observed_at = device.observed_at
            if row.active_lease_token is None:
                row.state = device.state.value
        for row in existing:
            if row.gpu_id not in observed and row.active_lease_token is None:
                row.state = GPUDeviceState.OFFLINE.value

    def get_active_lease(self, job_id: str, step_id: str) -> GPULease | None:
        moment = utc_now()
        with self._session_factory() as session:
            row = session.scalar(
                select(GPULeaseRow).where(
                    GPULeaseRow.job_id == job_id,
                    GPULeaseRow.step_id == step_id,
                    GPULeaseRow.status == "active",
                    GPULeaseRow.expires_at > moment,
                )
            )
            return None if row is None else self._lease_from_row(row)

    def resolve(self, run_id: str, step_id: str) -> GPULease | None:
        """Task 10 lease-provider boundary keyed by the exact runtime owner."""
        moment = utc_now()
        with self._session_factory() as session:
            row = session.scalar(
                select(GPULeaseRow).where(
                    GPULeaseRow.run_id == run_id,
                    GPULeaseRow.step_id == step_id,
                    GPULeaseRow.status == "active",
                    GPULeaseRow.expires_at > moment,
                )
            )
            return None if row is None else self._lease_from_row(row)

    def active_leases_for_job(self, job_id: str) -> tuple[GPULease, ...]:
        moment = utc_now()
        with self._session_factory() as session:
            rows = session.scalars(
                select(GPULeaseRow)
                .where(
                    GPULeaseRow.job_id == job_id,
                    GPULeaseRow.status == "active",
                    GPULeaseRow.expires_at > moment,
                )
                .order_by(GPULeaseRow.created_at, GPULeaseRow.lease_token)
            )
            return tuple(self._lease_from_row(row) for row in rows)

    def complete_job(self, job_id: str, worker_id: str, *, now=None) -> None:
        """Idempotently close active and waiting GPU requests for a terminal job."""
        moment = now or utc_now()
        with self._session_factory.begin() as session:
            job_status = session.scalar(
                select(ReproductionJobRow.status)
                .where(ReproductionJobRow.job_id == job_id)
                .with_for_update()
            )
            terminal = {
                ReproductionJobStatus.SUCCEEDED.value,
                ReproductionJobStatus.FAILED.value,
                ReproductionJobStatus.CANCELLED.value,
            }
            if job_status not in terminal:
                raise GPULeaseLostError(
                    "GPU cleanup requires an authoritative terminal job transition"
                )
            requests = tuple(
                session.scalars(
                    select(GPUSchedulingRequestRow)
                    .where(GPUSchedulingRequestRow.job_id == job_id)
                    .with_for_update()
                )
            )
            for request in requests:
                if request.active_lease_token is not None:
                    lease = session.scalar(
                        select(GPULeaseRow)
                        .where(GPULeaseRow.lease_token == request.active_lease_token)
                        .with_for_update()
                    )
                    if lease is not None and lease.status == "active":
                        if lease.worker_id != worker_id:
                            raise GPULeaseLostError(
                                "terminal job GPU lease belongs to another worker"
                            )
                        self._clear_devices(session, lease.lease_token)
                        lease.status = "released"
                        lease.released_at = moment
                request.status = GPURequestStatus.COMPLETED.value
                request.active_lease_token = None
                self._sync_request_json(request)

    def complete_step(self, job_id: str, step_id: str, worker_id: str, *, now=None) -> None:
        """Release exactly one completed step lease without reopening its request."""
        moment = now or utc_now()
        with self._session_factory.begin() as session:
            row = session.scalar(
                select(GPULeaseRow)
                .where(
                    GPULeaseRow.job_id == job_id,
                    GPULeaseRow.step_id == step_id,
                    GPULeaseRow.status == "active",
                )
                .with_for_update()
            )
            if row is None:
                return
            if row.worker_id != worker_id:
                raise GPULeaseLostError("completed step GPU lease belongs to another worker")
            self._clear_devices(session, row.lease_token)
            request_row = session.get(GPUSchedulingRequestRow, row.request_id)
            if request_row is not None:
                request_row.status = GPURequestStatus.COMPLETED.value
                request_row.active_lease_token = None
                self._sync_request_json(request_row)
            row.status = "released"
            row.released_at = moment

    @staticmethod
    def _active_owned_lease(session, token, worker_id, moment):
        row = session.scalar(
            select(GPULeaseRow)
            .where(GPULeaseRow.lease_token == token)
            .with_for_update()
        )
        if row is None:
            raise PersistenceEntityNotFoundError(f"unknown GPU lease {token!r}")
        if row.worker_id != worker_id or row.status != "active" or row.expires_at <= moment:
            raise GPULeaseLostError("GPU lease is expired, released, or owned by another worker")
        return row

    @staticmethod
    def _clear_devices(session, token):
        rows = session.scalars(
            select(GPUDeviceRow)
            .where(GPUDeviceRow.active_lease_token == token)
            .with_for_update()
        )
        for device in rows:
            device.active_lease_token = None
            device.state = GPUDeviceState.AVAILABLE.value

    @staticmethod
    def _device_row(value):
        return GPUDeviceRow(
            gpu_id=value.gpu_id,
            total_memory_mb=value.total_memory_mb,
            available_memory_mb=value.available_memory_mb,
            state=value.state.value,
            model_name=value.model_name,
            evidence_json=list(value.evidence),
            observed_at=value.observed_at,
        )

    @staticmethod
    def _device_from_row(row):
        return GPUDevice(
            gpu_id=row.gpu_id,
            total_memory_mb=row.total_memory_mb,
            available_memory_mb=row.available_memory_mb,
            state=row.state,
            model_name=row.model_name,
            evidence=tuple(row.evidence_json),
            observed_at=row.observed_at,
        )

    @staticmethod
    def _request_row(value):
        return GPUSchedulingRequestRow(
            request_id=value.request_id,
            job_id=value.job_id,
            run_id=value.run_id,
            step_id=value.step_id,
            status=value.status.value,
            minimum_gpu_count=value.requirement.minimum_gpu_count,
            preferred_gpu_count=value.requirement.preferred_gpu_count,
            estimated_memory_mb=value.requirement.estimated_memory_mb,
            queued_at=value.queued_at,
            skip_count=value.skip_count,
            active_lease_token=value.active_lease_token,
            request_json=serialize_domain(value),
        )

    @staticmethod
    def _request_from_row(row):
        payload = dict(row.request_json)
        payload.update(
            status=row.status,
            queued_at=row.queued_at,
            skip_count=row.skip_count,
            active_lease_token=row.active_lease_token,
        )
        requirement = dict(payload["requirement"])
        requirement.update(
            minimum_gpu_count=row.minimum_gpu_count,
            preferred_gpu_count=row.preferred_gpu_count,
            estimated_memory_mb=row.estimated_memory_mb,
        )
        payload["requirement"] = requirement
        return deserialize_domain(payload, GPUSchedulingRequest)

    @classmethod
    def _sync_request_json(cls, row):
        row.request_json = serialize_domain(cls._request_from_row(row))

    @staticmethod
    def _lease_from_row(row):
        return GPULease(
            lease_token=row.lease_token,
            job_id=row.job_id,
            run_id=row.run_id,
            step_id=row.step_id,
            worker_id=row.worker_id,
            allocated_gpu_ids=tuple(row.allocated_gpu_ids_json),
            created_at=row.created_at,
            expires_at=row.expires_at,
            heartbeat_at=row.heartbeat_at,
        )


class PostgresGPUWorkerResourcePort:
    """Coordinates Task 14 job deferral with Task 15 GPU lease release."""

    def __init__(self, scheduler: PostgresGPUScheduler, job_queue) -> None:
        self.scheduler = scheduler
        self.job_queue = job_queue

    def defer(
        self, job_id, step_id, worker_id, job_lease_token, requirement, *, now=None,
    ) -> None:
        lease = self.scheduler.get_active_lease(job_id, step_id)
        if lease is not None:
            if lease.worker_id != worker_id:
                raise GPULeaseLostError("resource-waiting GPU lease belongs to another worker")
        # First invalidate the worker's durable job ownership. If this CAS fails,
        # an expired owner is not allowed to mutate GPU state.
        self.job_queue.defer(
            job_id, worker_id, job_lease_token, now=now,
        )
        if lease is not None:
            self.scheduler.requeue(
                lease.lease_token, worker_id, requirement, now=now,
            )

    def release_job(self, job_id, worker_id, *, now=None) -> None:
        self.scheduler.complete_job(job_id, worker_id, now=now)


class PostgresGPUAwareJobQueue:
    """Task 14 queue facade which admits only a GPU-schedulable queued job."""

    def __init__(self, scheduler: PostgresGPUScheduler, job_queue) -> None:
        self.scheduler = scheduler
        self.job_queue = job_queue

    def claim(self, worker_id, *, lease_seconds, now=None):
        cancellation = self.job_queue.claim_cancel_requested(
            worker_id, lease_seconds=lease_seconds, now=now,
        )
        if cancellation is not None:
            return cancellation
        lease = self.scheduler.allocate_next(
            worker_id, lease_seconds=lease_seconds, now=now,
        )
        if lease is None:
            return self.job_queue.claim_without_gpu_request(
                worker_id, lease_seconds=lease_seconds, now=now,
            )
        job = self.job_queue.claim_job(
            lease.job_id, worker_id, lease_seconds=lease_seconds, now=now,
        )
        if job is None:
            request = self.scheduler.get_request_by_lease(lease.lease_token)
            self.scheduler.requeue(
                lease.lease_token, worker_id, request.requirement, now=now,
            )
        return job

    def heartbeat(self, job_id, worker_id, lease_token, *, lease_seconds, now=None):
        job = self.job_queue.heartbeat(
            job_id, worker_id, lease_token, lease_seconds=lease_seconds, now=now,
        )
        for lease in self.scheduler.active_leases_for_job(job_id):
            if lease.worker_id == worker_id:
                self.scheduler.heartbeat(
                    lease.lease_token, worker_id, lease_seconds=lease_seconds, now=now,
                )
        return job

    def __getattr__(self, name):
        return getattr(self.job_queue, name)
