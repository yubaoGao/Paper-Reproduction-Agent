"""PostgreSQL repository adapters and transaction unit of work."""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from datetime import datetime, timezone
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
)
from backend.app.orchestration.ports import ConcurrentRunUpdateError
from backend.app.services.persistence import PersistenceConflictError, PersistenceEntityNotFoundError

from .models import (
    ArtifactReferenceRow,
    AttemptRecordRow,
    ComparisonReportRow,
    FinalResultRow,
    PlanningSnapshotRow,
    ReproductionJobRow,
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
            return deserialize_domain(row.job_json, ReproductionJob)

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
            return tuple(deserialize_domain(row.job_json, ReproductionJob) for row in session.scalars(statement))


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

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None

    def __enter__(self):
        self._session = self._session_factory()
        self._session.begin()
        self.jobs = PostgresReproductionJobRepository(self._session_factory, self._session)
        self.planning_snapshots = PostgresPlanningSnapshotRepository(self._session_factory, self._session)
        self.runs = PostgresReproductionRunRepository(self._session_factory, self._session)
        self.final_results = PostgresFinalResultRepository(self._session_factory, self._session)
        self.comparisons = PostgresComparisonReportRepository(self._session_factory, self._session)
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

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory
        self.jobs = PostgresReproductionJobRepository(session_factory)
        self.planning_snapshots = PostgresPlanningSnapshotRepository(session_factory)
        self.runs = PostgresReproductionRunRepository(session_factory)
        self.final_results = PostgresFinalResultRepository(session_factory)
        self.comparisons = PostgresComparisonReportRepository(session_factory)

    def transaction(self) -> PostgresPersistenceUnitOfWork:
        return PostgresPersistenceUnitOfWork(self.session_factory)


def _job_values(job: ReproductionJob) -> dict:
    return {
        "paper_id": job.paper.id,
        "paper_title": job.paper.title,
        "user_goal": job.user_goal,
        "status": job.status.value,
        "selection_json": serialize_domain(job.selection),
        "job_json": serialize_domain(job),
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


def _job_row(job: ReproductionJob) -> ReproductionJobRow:
    return ReproductionJobRow(job_id=job.job_id, **_job_values(job))


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
