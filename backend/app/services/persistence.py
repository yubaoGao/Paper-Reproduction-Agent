"""SQL-free repository protocols for durable reproduction state."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from backend.app.domain.persistence import (
    AuthoritativePlanningSnapshot,
    PersistedComparisonReport,
    PersistedFinalResult,
    ReproductionJob,
    ReproductionJobStatus,
    ResultValidationStatus,
)
from backend.app.orchestration.ports import ReproductionRunRepository
from .job_queue import DurableJobQueue
from .external_resources import ResourceRegistry


class PersistenceEntityNotFoundError(LookupError):
    pass


class PersistenceConflictError(RuntimeError):
    pass


@runtime_checkable
class ReproductionJobRepository(Protocol):
    def create(self, job: ReproductionJob) -> None: ...
    def get(self, job_id: str) -> ReproductionJob: ...
    def update(self, job: ReproductionJob) -> None: ...
    def list(self, *, status: ReproductionJobStatus | None = None) -> tuple[ReproductionJob, ...]: ...


@runtime_checkable
class PlanningSnapshotRepository(Protocol):
    def create(self, snapshot: AuthoritativePlanningSnapshot) -> None: ...
    def get(self, snapshot_id: str) -> AuthoritativePlanningSnapshot: ...
    def get_by_job(self, job_id: str) -> AuthoritativePlanningSnapshot: ...
    def get_by_plan(self, plan_id: str) -> AuthoritativePlanningSnapshot: ...


@runtime_checkable
class FinalResultRepository(Protocol):
    def create(self, result: PersistedFinalResult) -> None: ...
    def get(self, result_id: str) -> PersistedFinalResult: ...
    def update_validation(self, result_id: str, status: ResultValidationStatus) -> PersistedFinalResult: ...
    def list_by_job(self, job_id: str) -> tuple[PersistedFinalResult, ...]: ...
    def list_by_run(self, run_id: str) -> tuple[PersistedFinalResult, ...]: ...


@runtime_checkable
class ComparisonReportRepository(Protocol):
    def create(self, report: PersistedComparisonReport) -> None: ...
    def get(self, report_id: str) -> PersistedComparisonReport: ...
    def update(self, report: PersistedComparisonReport) -> None: ...
    def list_by_job(self, job_id: str) -> tuple[PersistedComparisonReport, ...]: ...


@runtime_checkable
class PersistenceUnitOfWork(Protocol):
    jobs: ReproductionJobRepository
    planning_snapshots: PlanningSnapshotRepository
    runs: ReproductionRunRepository
    final_results: FinalResultRepository
    comparisons: ComparisonReportRepository
    queue: DurableJobQueue
    resources: ResourceRegistry

    def __enter__(self) -> "PersistenceUnitOfWork": ...
    def __exit__(self, exc_type, exc, traceback) -> bool: ...
