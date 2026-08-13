"""SQLAlchemy 2.x mappings for production PostgreSQL persistence."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class PersistenceBase(DeclarativeBase):
    pass


class ReproductionJobRow(PersistenceBase):
    __tablename__ = "reproduction_jobs"
    __table_args__ = (
        Index("ix_reproduction_jobs_queue_order", "status", "enqueued_at", "job_id"),
    )

    job_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    paper_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    paper_title: Mapped[str] = mapped_column(Text, nullable=False)
    user_goal: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    enqueued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    worker_id: Mapped[str | None] = mapped_column(String(255), index=True)
    lease_token: Mapped[str | None] = mapped_column(String(64))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claim_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    selection_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    job_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class PlanningSnapshotRow(PersistenceBase):
    __tablename__ = "reproduction_planning_snapshots"

    snapshot_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("reproduction_jobs.job_id", ondelete="CASCADE"), nullable=False, unique=True)
    specification_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    plan_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    snapshot_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReproductionRunRow(PersistenceBase):
    __tablename__ = "reproduction_runs"

    run_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("reproduction_jobs.job_id", ondelete="CASCADE"), nullable=False, index=True)
    plan_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    manifest_digest: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    run_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class StepRunRow(PersistenceBase):
    __tablename__ = "reproduction_step_runs"

    run_id: Mapped[str] = mapped_column(ForeignKey("reproduction_runs.run_id", ondelete="CASCADE"), primary_key=True)
    step_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    experiment_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    action_type: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False)
    step_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AttemptRecordRow(PersistenceBase):
    __tablename__ = "reproduction_attempt_records"
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "step_id"],
            ["reproduction_step_runs.run_id", "reproduction_step_runs.step_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("run_id", "step_id", "attempt_number", name="uq_attempt_owner_number"),
    )

    run_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    step_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    attempt_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    command_id: Mapped[str] = mapped_column(String(255), nullable=False)
    attempt_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ArtifactReferenceRow(PersistenceBase):
    __tablename__ = "reproduction_artifact_references"
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "step_id"],
            ["reproduction_step_runs.run_id", "reproduction_step_runs.step_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["run_id", "step_id", "attempt_number"],
            [
                "reproduction_attempt_records.run_id",
                "reproduction_attempt_records.step_id",
                "reproduction_attempt_records.attempt_number",
            ],
            ondelete="CASCADE",
        ),
    )

    artifact_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("reproduction_runs.run_id", ondelete="CASCADE"), nullable=False, index=True)
    step_id: Mapped[str | None] = mapped_column(String(255), index=True)
    attempt_number: Mapped[int | None] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    uri: Mapped[str] = mapped_column(Text, nullable=False)
    checksum: Mapped[str | None] = mapped_column(String(255))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False)


class FinalResultRow(PersistenceBase):
    __tablename__ = "reproduction_final_results"

    result_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("reproduction_jobs.job_id", ondelete="CASCADE"), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("reproduction_runs.run_id", ondelete="CASCADE"), nullable=False, index=True)
    paper_experiment_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    validation_status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    result_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ComparisonReportRow(PersistenceBase):
    __tablename__ = "reproduction_comparison_reports"

    report_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("reproduction_jobs.job_id", ondelete="CASCADE"), nullable=False, index=True)
    report_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
