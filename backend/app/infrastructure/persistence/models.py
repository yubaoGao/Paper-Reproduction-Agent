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


class ExternalResourceBindingRow(PersistenceBase):
    __tablename__ = "external_resource_bindings"
    __table_args__ = (
        Index(
            "ix_external_resource_identity",
            "resource_type", "canonical_key", "owner_principal", "shared",
        ),
    )

    resource_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_key: Mapped[str] = mapped_column(Text, nullable=False)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    host_path: Mapped[str] = mapped_column(Text, nullable=False)
    access: Mapped[str] = mapped_column(String(32), nullable=False)
    owner_principal: Mapped[str | None] = mapped_column(String(255), index=True)
    shared: Mapped[bool] = mapped_column(nullable=False, default=False, index=True)
    validation_status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    binding_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class GPUDeviceRow(PersistenceBase):
    __tablename__ = "gpu_devices"

    gpu_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    total_memory_mb: Mapped[int] = mapped_column(Integer, nullable=False)
    available_memory_mb: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    model_name: Mapped[str | None] = mapped_column(Text)
    evidence_json: Mapped[list] = mapped_column(JSONB, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    active_lease_token: Mapped[str | None] = mapped_column(String(64), index=True)


class GPUSchedulingRequestRow(PersistenceBase):
    __tablename__ = "gpu_scheduling_requests"
    __table_args__ = (
        Index("ix_gpu_requests_wait_order", "status", "queued_at", "request_id"),
        UniqueConstraint("job_id", "run_id", "step_id", name="uq_gpu_request_owner"),
    )

    request_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("reproduction_jobs.job_id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    step_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    minimum_gpu_count: Mapped[int] = mapped_column(Integer, nullable=False)
    preferred_gpu_count: Mapped[int] = mapped_column(Integer, nullable=False)
    estimated_memory_mb: Mapped[int | None] = mapped_column(Integer)
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    skip_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active_lease_token: Mapped[str | None] = mapped_column(String(64), index=True)
    request_json: Mapped[dict] = mapped_column(JSONB, nullable=False)


class GPULeaseRow(PersistenceBase):
    __tablename__ = "gpu_leases"

    lease_token: Mapped[str] = mapped_column(String(64), primary_key=True)
    request_id: Mapped[str] = mapped_column(
        ForeignKey("gpu_scheduling_requests.request_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    job_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    step_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    worker_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    allocated_gpu_ids_json: Mapped[list] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
