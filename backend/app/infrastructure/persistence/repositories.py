"""PostgreSQL repository adapters and transaction unit of work."""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.domain import (
    Artifact,
    ArtifactReference,
    AuthoritativePlanningSnapshot,
    FinalResult,
    PersistedComparisonReport,
    PersistedFinalResult,
    ReproductionComparisonReport,
    ReproductionJob,
    ReproductionJobStatus,
    ReproductionRun,
    ResultValidationStatus,
    RunStatus,
    ReproductionEvent,
    ReproductionEventType,
    ReproductionIntake,
    RepositorySnapshot,
)
from backend.app.orchestration.ports import ConcurrentRunUpdateError
from backend.app.services.persistence import PersistenceConflictError, PersistenceEntityNotFoundError

from .models import (
    ArtifactReferenceRow,
    AttemptRecordRow,
    ComparisonReportRow,
    FinalResultRow,
    PlanningSnapshotRow,
    ReproductionJobRow, RepositorySnapshotRegistrationRow,
    ReproductionIntakeRow,
    ReproductionEventRow,
    ReproductionRunRow,
    StepRunRow,
)
from .serialization import deserialize_domain, serialize_domain


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class _Repository:
    def __init__(self, session_factory: sessionmaker[Session], session: Session | None = None) -> None:
        self._session_factory = session_factory
        self._session = session

    @contextmanager
    def _read(self) -> Iterator[Session]:
        if self._session is not None:
            yield self._session
            return
        with self._session_factory() as session:
            yield session

    @contextmanager
    def _write(self) -> Iterator[Session]:
        if self._session is not None:
            yield self._session
            return
        with self._session_factory.begin() as session:
            yield session

    @staticmethod
    def _conflict(label: str, exc: IntegrityError) -> PersistenceConflictError:
        return PersistenceConflictError(f"{label} already exists or violates persistence constraints")


class PostgresReproductionJobRepository(_Repository):
    def create(self, job: ReproductionJob) -> None:
        try:
            with self._write() as session:
                session.add(_job_row(job))
                session.flush()
        except IntegrityError as exc:
            raise self._conflict(f"job {job.job_id!r}", exc) from exc

    def get(self, job_id: str) -> ReproductionJob:
        with self._read() as session:
            row = session.get(ReproductionJobRow, job_id)
            if row is None:
                raise PersistenceEntityNotFoundError(f"unknown reproduction job {job_id!r}")
            return _job_from_row(row)

    def update(self, job: ReproductionJob) -> None:
        with self._write() as session:
            values = _job_values(job)
            result = session.execute(
                update(ReproductionJobRow).where(ReproductionJobRow.job_id == job.job_id).values(**values)
            )
            if result.rowcount != 1:
                raise PersistenceEntityNotFoundError(f"unknown reproduction job {job.job_id!r}")

    def list(self, *, status: ReproductionJobStatus | None = None) -> tuple[ReproductionJob, ...]:
        statement = select(ReproductionJobRow).order_by(ReproductionJobRow.created_at, ReproductionJobRow.job_id)
        if status is not None:
            statement = statement.where(ReproductionJobRow.status == status.value)
        with self._read() as session:
            return tuple(_job_from_row(row) for row in session.scalars(statement))

    def list_by_owner(self, owner_principal: str) -> tuple[ReproductionJob, ...]:
        statement = (
            select(ReproductionJobRow)
            .where(ReproductionJobRow.owner_principal == owner_principal)
            .order_by(ReproductionJobRow.created_at, ReproductionJobRow.job_id)
        )
        with self._read() as session:
            return tuple(_job_from_row(row) for row in session.scalars(statement))


class PostgresReproductionIntakeRepository(_Repository):
    def create(self, intake: ReproductionIntake) -> None:
        try:
            with self._write() as session:
                session.add(ReproductionIntakeRow(
                    intake_id=intake.intake_id,
                    owner_principal=intake.owner_principal,
                    state=intake.state.value,
                    job_id=intake.job_id,
                    intake_json=serialize_domain(intake),
                    created_at=intake.created_at,
                    updated_at=intake.updated_at,
                ))
                session.flush()
        except IntegrityError as exc:
            raise self._conflict(f"intake {intake.intake_id!r}", exc) from exc

    def get(self, intake_id: str) -> ReproductionIntake:
        with self._read() as session:
            row = session.get(ReproductionIntakeRow, intake_id)
            if row is None:
                raise PersistenceEntityNotFoundError(f"unknown reproduction intake {intake_id!r}")
            return deserialize_domain(row.intake_json, ReproductionIntake)

    def update(self, intake: ReproductionIntake) -> None:
        with self._write() as session:
            result = session.execute(
                update(ReproductionIntakeRow)
                .where(ReproductionIntakeRow.intake_id == intake.intake_id)
                .values(
                    owner_principal=intake.owner_principal,
                    state=intake.state.value,
                    job_id=intake.job_id,
                    intake_json=serialize_domain(intake),
                    updated_at=intake.updated_at,
                )
            )
            if result.rowcount != 1:
                raise PersistenceEntityNotFoundError(f"unknown reproduction intake {intake.intake_id!r}")

    def list_by_owner(self, owner_principal: str) -> tuple[ReproductionIntake, ...]:
        statement = (
            select(ReproductionIntakeRow)
            .where(ReproductionIntakeRow.owner_principal == owner_principal)
            .order_by(ReproductionIntakeRow.created_at, ReproductionIntakeRow.intake_id)
        )
        with self._read() as session:
            return tuple(deserialize_domain(row.intake_json, ReproductionIntake) for row in session.scalars(statement))


class PostgresReproductionEventRepository(_Repository):
    def append(self, *, intake_id, owner_principal, event_type, payload, job_id=None):
        # Domain validation happens before inserting untrusted product payloads.
        draft = ReproductionEvent(
            event_id="pending:event", sequence=1, intake_id=intake_id,
            job_id=job_id, owner_principal=owner_principal,
            event_type=event_type, payload=payload,
        )
        with self._write() as session:
            row = ReproductionEventRow(
                event_id=f"pending:{hashlib.sha256((intake_id + str(utc_now())).encode()).hexdigest()}",
                intake_id=intake_id, job_id=job_id, owner_principal=owner_principal,
                event_type=event_type.value, payload_json=draft.payload,
                created_at=draft.created_at,
            )
            session.add(row)
            session.flush()
            row.event_id = f"event:{row.sequence}"
            session.flush()
            return self._event(row)

    def list_by_intake(self, intake_id: str, *, after_sequence: int = 0):
        return self._list(
            select(ReproductionEventRow).where(
                ReproductionEventRow.intake_id == intake_id,
                ReproductionEventRow.sequence > after_sequence,
            )
        )

    def list_by_job(self, job_id: str, *, after_sequence: int = 0):
        # Include analysis events written before the durable job was created.
        intake_id = select(ReproductionIntakeRow.intake_id).where(ReproductionIntakeRow.job_id == job_id).scalar_subquery()
        return self._list(
            select(ReproductionEventRow).where(
                ReproductionEventRow.intake_id == intake_id,
                ReproductionEventRow.sequence > after_sequence,
            )
        )

    def bind_job(self, intake_id: str, job_id: str) -> None:
        with self._write() as session:
            session.execute(
                update(ReproductionEventRow)
                .where(
                    ReproductionEventRow.intake_id == intake_id,
                    ReproductionEventRow.job_id.is_(None),
                )
                .values(job_id=job_id)
            )

    def _list(self, statement):
        with self._read() as session:
            rows = session.scalars(statement.order_by(ReproductionEventRow.sequence))
            return tuple(self._event(row) for row in rows)

    @staticmethod
    def _event(row):
        return ReproductionEvent(
            event_id=row.event_id, sequence=row.sequence, intake_id=row.intake_id,
            job_id=row.job_id, owner_principal=row.owner_principal,
            event_type=ReproductionEventType(row.event_type), payload=row.payload_json,
            created_at=row.created_at,
        )


class PostgresRepositorySnapshotRegistry(_Repository):
    """Durable trusted mapping consumed only by sandbox composition."""

    def register(self, snapshot: RepositorySnapshot) -> None:
        host_path = str(Path(snapshot.root).resolve(strict=True))
        if not Path(host_path).is_dir():
            raise ValueError("repository snapshot root must be a directory")
        with self._write() as session:
            existing = session.get(RepositorySnapshotRegistrationRow, snapshot.snapshot_id)
            if existing is not None:
                if (
                    existing.host_path != host_path
                    or existing.content_hash != snapshot.content_hash
                    or existing.resolved_commit_sha != snapshot.resolved_commit_sha
                ):
                    raise PersistenceConflictError(
                        "repository snapshot ID is registered with different immutable content"
                    )
                return
            session.add(RepositorySnapshotRegistrationRow(
                snapshot_id=snapshot.snapshot_id,
                repository_id=snapshot.repository.repository_id,
                resolved_commit_sha=snapshot.resolved_commit_sha,
                content_hash=snapshot.content_hash,
                host_path=host_path,
                snapshot_json=serialize_domain(snapshot),
                created_at=utc_now(),
            ))

    def get(self, snapshot_id: str) -> RepositorySnapshot:
        with self._read() as session:
            row = session.get(RepositorySnapshotRegistrationRow, snapshot_id)
            if row is None:
                raise PersistenceEntityNotFoundError(
                    f"unknown repository snapshot {snapshot_id!r}"
                )
            payload = dict(row.snapshot_json)
            payload["root"] = row.host_path
            return deserialize_domain(payload, RepositorySnapshot)


class PostgresPlanningSnapshotRepository(_Repository):
    def create(self, snapshot: AuthoritativePlanningSnapshot) -> None:
        try:
            with self._write() as session:
                session.add(
                    PlanningSnapshotRow(
                        snapshot_id=snapshot.snapshot_id,
                        job_id=snapshot.job_id,
                        specification_id=snapshot.specification.id,
                        plan_id=snapshot.execution_plan.plan_id,
                        snapshot_json=serialize_domain(snapshot),
                        created_at=snapshot.created_at,
                    )
                )
                session.flush()
        except IntegrityError as exc:
            raise self._conflict(f"planning snapshot {snapshot.snapshot_id!r}", exc) from exc

    def get(self, snapshot_id: str) -> AuthoritativePlanningSnapshot:
        return self._one(PlanningSnapshotRow.snapshot_id == snapshot_id, snapshot_id)

    def get_by_job(self, job_id: str) -> AuthoritativePlanningSnapshot:
        return self._one(PlanningSnapshotRow.job_id == job_id, f"job {job_id}")

    def get_by_plan(self, plan_id: str) -> AuthoritativePlanningSnapshot:
        return self._one(PlanningSnapshotRow.plan_id == plan_id, f"plan {plan_id}")

    def _one(self, predicate, identity: str) -> AuthoritativePlanningSnapshot:
        with self._read() as session:
            row = session.scalar(select(PlanningSnapshotRow).where(predicate))
            if row is None:
                raise PersistenceEntityNotFoundError(f"unknown planning snapshot for {identity!r}")
            return deserialize_domain(row.snapshot_json, AuthoritativePlanningSnapshot)


class PostgresReproductionRunRepository(_Repository):
    """Durable run aggregate repository with compare-and-swap revision updates."""

    def create(self, run: ReproductionRun) -> None:
        if run.revision != 0:
            raise PersistenceConflictError("new reproduction run must start at revision zero")
        try:
            with self._write() as session:
                job_id = self._job_for_plan(session, run.plan_id)
                session.add(_run_row(run, job_id))
                session.flush()
                self._replace_children(session, run, job_id)
                session.flush()
        except IntegrityError as exc:
            raise self._conflict(f"run {run.run_id!r}", exc) from exc

    def save(self, run: ReproductionRun, *, expected_revision: int) -> None:
        if run.revision != expected_revision + 1:
            raise ConcurrentRunUpdateError(
                f"run payload revision {run.revision} must be expected revision {expected_revision} plus one"
            )
        with self._write() as session:
            result = session.execute(
                update(ReproductionRunRow)
                .where(
                    ReproductionRunRow.run_id == run.run_id,
                    ReproductionRunRow.revision == expected_revision,
                )
                .values(**_run_values(run))
            )
            if result.rowcount != 1:
                exists = session.scalar(select(ReproductionRunRow.run_id).where(ReproductionRunRow.run_id == run.run_id))
                if exists is None:
                    raise PersistenceEntityNotFoundError(f"unknown reproduction run {run.run_id!r}")
                raise ConcurrentRunUpdateError(
                    f"reproduction run {run.run_id!r} was concurrently modified at revision {expected_revision}"
                )
            job_id = session.scalar(select(ReproductionRunRow.job_id).where(ReproductionRunRow.run_id == run.run_id))
            self._replace_children(session, run, job_id)
            session.flush()

    def get(self, run_id: str) -> ReproductionRun:
        with self._read() as session:
            row = session.get(ReproductionRunRow, run_id)
            if row is None:
                raise PersistenceEntityNotFoundError(f"unknown reproduction run {run_id!r}")
            return deserialize_domain(row.run_json, ReproductionRun)

    def list_by_job(self, job_id: str) -> tuple[ReproductionRun, ...]:
        return self._list(select(ReproductionRunRow).where(ReproductionRunRow.job_id == job_id))

    def list_by_status(self, status: str | RunStatus) -> tuple[ReproductionRun, ...]:
        value = status.value if isinstance(status, RunStatus) else status
        return self._list(select(ReproductionRunRow).where(ReproductionRunRow.status == value))

    def _list(self, statement) -> tuple[ReproductionRun, ...]:
        statement = statement.order_by(ReproductionRunRow.created_at, ReproductionRunRow.run_id)
        with self._read() as session:
            return tuple(deserialize_domain(row.run_json, ReproductionRun) for row in session.scalars(statement))

    @staticmethod
    def _job_for_plan(session: Session, plan_id: str) -> str:
        job_id = session.scalar(select(PlanningSnapshotRow.job_id).where(PlanningSnapshotRow.plan_id == plan_id))
        if job_id is None:
            raise PersistenceEntityNotFoundError(f"run plan {plan_id!r} has no authoritative planning snapshot")
        return job_id

    def _replace_children(self, session: Session, run: ReproductionRun, job_id: str) -> None:
        session.execute(delete(AttemptRecordRow).where(AttemptRecordRow.run_id == run.run_id))
        session.execute(delete(StepRunRow).where(StepRunRow.run_id == run.run_id))
        session.execute(delete(ArtifactReferenceRow).where(ArtifactReferenceRow.run_id == run.run_id))
        for step in run.steps:
            session.add(
                StepRunRow(
                    run_id=run.run_id,
                    step_id=step.step_id,
                    experiment_id=step.experiment_id,
                    action_type=step.action_type.value if step.action_type else None,
                    status=step.status.value,
                    attempt_count=len(step.attempts),
                    step_json=serialize_domain(step),
                    started_at=step.started_at,
                    finished_at=step.finished_at,
                )
            )
            for attempt in step.attempts:
                session.add(
                    AttemptRecordRow(
                        run_id=run.run_id,
                        step_id=step.step_id,
                        attempt_number=attempt.attempt_number,
                        status=attempt.status.value,
                        command_id=attempt.command_id,
                        attempt_json=serialize_domain(attempt),
                        started_at=attempt.started_at,
                        finished_at=attempt.finished_at,
                    )
                )
        for owner_step, owner_attempt, artifact in _artifacts(run):
            session.add(_artifact_row(run.run_id, owner_step, owner_attempt, artifact))
        self._upsert_embedded_results(session, run, job_id)

    @staticmethod
    def _upsert_embedded_results(session: Session, run: ReproductionRun, job_id: str) -> None:
        for result in run.final_results:
            existing = session.get(FinalResultRow, result.result_id)
            if existing is None:
                now = utc_now()
                session.add(
                    FinalResultRow(
                        result_id=result.result_id,
                        job_id=job_id,
                        run_id=run.run_id,
                        paper_experiment_id=result.paper_experiment_id,
                        validation_status=ResultValidationStatus.PENDING.value,
                        result_json=serialize_domain(result),
                        created_at=now,
                        updated_at=now,
                    )
                )
            else:
                existing.result_json = serialize_domain(result)
                existing.paper_experiment_id = result.paper_experiment_id
                existing.updated_at = utc_now()


class PostgresFinalResultRepository(_Repository):
    def create(self, persisted: PersistedFinalResult) -> None:
        try:
            with self._write() as session:
                session.add(_final_result_row(persisted))
                session.flush()
        except IntegrityError as exc:
            raise self._conflict(f"FinalResult {persisted.result.result_id!r}", exc) from exc

    def get(self, result_id: str) -> PersistedFinalResult:
        with self._read() as session:
            row = session.get(FinalResultRow, result_id)
            if row is None:
                raise PersistenceEntityNotFoundError(f"unknown FinalResult {result_id!r}")
            return _persisted_final_result(row)

    def update_validation(self, result_id: str, status: ResultValidationStatus) -> PersistedFinalResult:
        with self._write() as session:
            result = session.execute(
                update(FinalResultRow)
                .where(FinalResultRow.result_id == result_id)
                .values(validation_status=status.value, updated_at=utc_now())
            )
            if result.rowcount != 1:
                raise PersistenceEntityNotFoundError(f"unknown FinalResult {result_id!r}")
            row = session.get(FinalResultRow, result_id)
            return _persisted_final_result(row)

    def list_by_job(self, job_id: str) -> tuple[PersistedFinalResult, ...]:
        return self._list(select(FinalResultRow).where(FinalResultRow.job_id == job_id))

    def list_by_run(self, run_id: str) -> tuple[PersistedFinalResult, ...]:
        return self._list(select(FinalResultRow).where(FinalResultRow.run_id == run_id))

    def _list(self, statement) -> tuple[PersistedFinalResult, ...]:
        statement = statement.order_by(FinalResultRow.created_at, FinalResultRow.result_id)
        with self._read() as session:
            return tuple(_persisted_final_result(row) for row in session.scalars(statement))


class PostgresComparisonReportRepository(_Repository):
    def create(self, persisted: PersistedComparisonReport) -> None:
        try:
            with self._write() as session:
                session.add(_comparison_row(persisted))
                session.flush()
        except IntegrityError as exc:
            raise self._conflict(f"comparison report {persisted.report.report_id!r}", exc) from exc

    def get(self, report_id: str) -> PersistedComparisonReport:
        with self._read() as session:
            row = session.get(ComparisonReportRow, report_id)
            if row is None:
                raise PersistenceEntityNotFoundError(f"unknown comparison report {report_id!r}")
            return _persisted_comparison(row)

    def update(self, persisted: PersistedComparisonReport) -> None:
        with self._write() as session:
            result = session.execute(
                update(ComparisonReportRow)
                .where(ComparisonReportRow.report_id == persisted.report.report_id)
                .values(report_json=serialize_domain(persisted.report), updated_at=persisted.updated_at)
            )
            if result.rowcount != 1:
                raise PersistenceEntityNotFoundError(f"unknown comparison report {persisted.report.report_id!r}")

    def list_by_job(self, job_id: str) -> tuple[PersistedComparisonReport, ...]:
        statement = (
            select(ComparisonReportRow)
            .where(ComparisonReportRow.job_id == job_id)
            .order_by(ComparisonReportRow.created_at, ComparisonReportRow.report_id)
        )
        with self._read() as session:
            return tuple(_persisted_comparison(row) for row in session.scalars(statement))


class PostgresPersistenceUnitOfWork:
    """One local PostgreSQL transaction shared by all persistence repositories."""

    def __init__(self, session_factory: sessionmaker[Session], resource_path_validator=None) -> None:
        self._session_factory = session_factory
        self._resource_path_validator = resource_path_validator
        self._session: Session | None = None

    def __enter__(self):
        from .job_queue import PostgresDurableJobQueue
        from .resource_registry import PostgresResourceRegistry

        self._session = self._session_factory()
        self._session.begin()
        self.jobs = PostgresReproductionJobRepository(self._session_factory, self._session)
        self.intakes = PostgresReproductionIntakeRepository(self._session_factory, self._session)
        self.events = PostgresReproductionEventRepository(self._session_factory, self._session)
        self.planning_snapshots = PostgresPlanningSnapshotRepository(self._session_factory, self._session)
        self.runs = PostgresReproductionRunRepository(self._session_factory, self._session)
        self.final_results = PostgresFinalResultRepository(self._session_factory, self._session)
        self.comparisons = PostgresComparisonReportRepository(self._session_factory, self._session)
        self.queue = PostgresDurableJobQueue(self._session_factory, self._session)
        self.resources = PostgresResourceRegistry(
            self._session_factory, self._session,
            path_validator=self._resource_path_validator,
        )
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        if self._session is None:
            return False
        try:
            if exc_type is None:
                self._session.commit()
            else:
                self._session.rollback()
        finally:
            self._session.close()
            self._session = None
        return False


class PostgresPersistence:
    """Repository bundle; construct again with the same database to recover state."""

    def __init__(
        self, session_factory: sessionmaker[Session], *, gpu_inventory_provider=None,
        external_resource_path_validator=None,
    ) -> None:
        from .job_queue import PostgresDurableJobQueue
        from .resource_registry import PostgresResourceRegistry
        from .gpu_scheduler import (
            PostgresGPUAwareJobQueue,PostgresGPUScheduler,
            PostgresGPUWorkerResourcePort,
        )
        if gpu_inventory_provider is None:
            from backend.app.infrastructure.gpu import NvidiaSMIInventoryProvider
            gpu_inventory_provider = NvidiaSMIInventoryProvider()

        self.session_factory = session_factory
        self.jobs = PostgresReproductionJobRepository(session_factory)
        self.intakes = PostgresReproductionIntakeRepository(session_factory)
        self.events = PostgresReproductionEventRepository(session_factory)
        self.planning_snapshots = PostgresPlanningSnapshotRepository(session_factory)
        self.repository_snapshots = PostgresRepositorySnapshotRegistry(session_factory)
        self.runs = PostgresReproductionRunRepository(session_factory)
        self.final_results = PostgresFinalResultRepository(session_factory)
        self.comparisons = PostgresComparisonReportRepository(session_factory)
        self.queue = PostgresDurableJobQueue(session_factory)
        self.external_resource_path_validator = external_resource_path_validator
        self.resources = PostgresResourceRegistry(
            session_factory, path_validator=external_resource_path_validator,
        )
        self.gpu_scheduler = PostgresGPUScheduler(
            session_factory, inventory_provider=gpu_inventory_provider,
        )
        self.gpu_queue = PostgresGPUAwareJobQueue(self.gpu_scheduler, self.queue)
        self.gpu_resources = PostgresGPUWorkerResourcePort(
            self.gpu_scheduler, self.queue,
        )

    def transaction(self) -> PostgresPersistenceUnitOfWork:
        return PostgresPersistenceUnitOfWork(
            self.session_factory, self.external_resource_path_validator,
        )


class PostgresProductPersistence:
    """API-facing PostgreSQL bundle with no GPU inventory or execution adapters."""

    def __init__(self, session_factory: sessionmaker[Session], *, external_resource_path_validator=None) -> None:
        from .job_queue import PostgresDurableJobQueue
        from .resource_registry import PostgresResourceRegistry

        self.session_factory = session_factory
        self.intakes = PostgresReproductionIntakeRepository(session_factory)
        self.events = PostgresReproductionEventRepository(session_factory)
        self.jobs = PostgresReproductionJobRepository(session_factory)
        self.planning_snapshots = PostgresPlanningSnapshotRepository(session_factory)
        self.repository_snapshots = PostgresRepositorySnapshotRegistry(session_factory)
        self.runs = PostgresReproductionRunRepository(session_factory)
        self.final_results = PostgresFinalResultRepository(session_factory)
        self.comparisons = PostgresComparisonReportRepository(session_factory)
        self.queue = PostgresDurableJobQueue(session_factory)
        self.resources = PostgresResourceRegistry(
            session_factory, path_validator=external_resource_path_validator,
        )


def _job_values(job: ReproductionJob) -> dict:
    return {
        "owner_principal": job.owner_principal,
        "paper_id": job.paper.id,
        "paper_title": job.paper.title,
        "user_goal": job.user_goal,
        "status": job.status.value,
        "enqueued_at": job.enqueued_at,
        "worker_id": job.worker_id,
        "lease_token": job.lease_token,
        "claimed_at": job.claimed_at,
        "lease_expires_at": job.lease_expires_at,
        "heartbeat_at": job.heartbeat_at,
        "claim_count": job.claim_count,
        "last_error": job.last_error,
        "selection_json": serialize_domain(job.selection),
        "job_json": serialize_domain(job),
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


def _job_row(job: ReproductionJob) -> ReproductionJobRow:
    return ReproductionJobRow(job_id=job.job_id, **_job_values(job))


def _job_from_row(row: ReproductionJobRow) -> ReproductionJob:
    payload = dict(row.job_json)
    payload.update(
        {
            "status": row.status,
            "enqueued_at": row.enqueued_at,
            "worker_id": row.worker_id,
            "lease_token": row.lease_token,
            "claimed_at": row.claimed_at,
            "lease_expires_at": row.lease_expires_at,
            "heartbeat_at": row.heartbeat_at,
            "claim_count": row.claim_count,
            "last_error": row.last_error,
            "updated_at": row.updated_at,
        }
    )
    return deserialize_domain(payload, ReproductionJob)


def _run_values(run: ReproductionRun) -> dict:
    return {
        "plan_id": run.plan_id,
        "manifest_digest": run.manifest.plan_digest,
        "status": run.status.value,
        "revision": run.revision,
        "run_json": serialize_domain(run),
        "created_at": run.created_at,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "updated_at": utc_now(),
    }


def _run_row(run: ReproductionRun, job_id: str) -> ReproductionRunRow:
    return ReproductionRunRow(run_id=run.run_id, job_id=job_id, **_run_values(run))


def _final_result_row(persisted: PersistedFinalResult) -> FinalResultRow:
    return FinalResultRow(
        result_id=persisted.result.result_id,
        job_id=persisted.job_id,
        run_id=persisted.run_id,
        paper_experiment_id=persisted.result.paper_experiment_id,
        validation_status=persisted.validation_status.value,
        result_json=serialize_domain(persisted.result),
        created_at=persisted.created_at,
        updated_at=persisted.updated_at,
    )


def _persisted_final_result(row: FinalResultRow) -> PersistedFinalResult:
    return PersistedFinalResult(
        job_id=row.job_id,
        run_id=row.run_id,
        result=deserialize_domain(row.result_json, FinalResult),
        validation_status=ResultValidationStatus(row.validation_status),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _comparison_row(persisted: PersistedComparisonReport) -> ComparisonReportRow:
    return ComparisonReportRow(
        report_id=persisted.report.report_id,
        job_id=persisted.job_id,
        report_json=serialize_domain(persisted.report),
        created_at=persisted.created_at,
        updated_at=persisted.updated_at,
    )


def _persisted_comparison(row: ComparisonReportRow) -> PersistedComparisonReport:
    return PersistedComparisonReport(
        job_id=row.job_id,
        report=deserialize_domain(row.report_json, ReproductionComparisonReport),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _artifacts(run: ReproductionRun) -> tuple[tuple[str | None, int | None, Artifact], ...]:
    values: list[tuple[str | None, int | None, Artifact]] = []
    values.extend((item.step_id, item.attempt_number, item.artifact) for item in run.artifacts)
    for step in run.steps:
        values.extend((item.step_id, item.attempt_number, item.artifact) for item in (*step.input_artifacts, *step.artifacts))
        for attempt in step.attempts:
            values.extend((item.step_id, item.attempt_number, item.artifact) for item in attempt.artifacts)
            values.extend((step.step_id, attempt.attempt_number, patch.artifact) for patch in attempt.patches if patch.artifact)
    unique: dict[str, tuple[str | None, int | None, Artifact]] = {}
    for step_id, attempt_number, artifact in values:
        key = _artifact_id(run.run_id, step_id, attempt_number, artifact)
        unique.setdefault(key, (step_id, attempt_number, artifact))
    return tuple(unique.values())


def _artifact_id(run_id: str, step_id: str | None, attempt_number: int | None, artifact: Artifact) -> str:
    value = "\x1f".join((run_id, step_id or "", str(attempt_number or ""), artifact.kind.value, artifact.name, artifact.uri, artifact.checksum or ""))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _artifact_row(run_id: str, step_id: str | None, attempt_number: int | None, artifact: Artifact) -> ArtifactReferenceRow:
    return ArtifactReferenceRow(
        artifact_id=_artifact_id(run_id, step_id, attempt_number, artifact),
        run_id=run_id,
        step_id=step_id,
        attempt_number=attempt_number,
        kind=artifact.kind.value,
        name=artifact.name,
        uri=artifact.uri,
        checksum=artifact.checksum,
        size_bytes=artifact.size_bytes,
        metadata_json=serialize_domain(artifact),
    )
