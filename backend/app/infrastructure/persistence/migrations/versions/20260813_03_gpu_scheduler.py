"""Add durable GPU inventory, scheduling requests, and leases.

Revision ID: 20260813_03
Revises: 20260813_02
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260813_03"
down_revision = "20260813_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gpu_devices",
        sa.Column("gpu_id", sa.String(255), primary_key=True),
        sa.Column("total_memory_mb", sa.Integer(), nullable=False),
        sa.Column("available_memory_mb", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("model_name", sa.Text()),
        sa.Column("evidence_json", postgresql.JSONB(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("active_lease_token", sa.String(64)),
    )
    op.create_index("ix_gpu_devices_state", "gpu_devices", ["state"])
    op.create_index("ix_gpu_devices_active_lease_token", "gpu_devices", ["active_lease_token"])
    op.create_table(
        "gpu_scheduling_requests",
        sa.Column("request_id", sa.String(255), primary_key=True),
        sa.Column("job_id", sa.String(255), sa.ForeignKey("reproduction_jobs.job_id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", sa.String(255), nullable=False),
        sa.Column("step_id", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("minimum_gpu_count", sa.Integer(), nullable=False),
        sa.Column("preferred_gpu_count", sa.Integer(), nullable=False),
        sa.Column("estimated_memory_mb", sa.Integer()),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("skip_count", sa.Integer(), nullable=False),
        sa.Column("active_lease_token", sa.String(64)),
        sa.Column("request_json", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("job_id", "run_id", "step_id", name="uq_gpu_request_owner"),
    )
    for name, columns in (
        ("ix_gpu_scheduling_requests_job_id", ["job_id"]),
        ("ix_gpu_scheduling_requests_run_id", ["run_id"]),
        ("ix_gpu_scheduling_requests_step_id", ["step_id"]),
        ("ix_gpu_scheduling_requests_status", ["status"]),
        ("ix_gpu_scheduling_requests_active_lease_token", ["active_lease_token"]),
        ("ix_gpu_requests_wait_order", ["status", "queued_at", "request_id"]),
    ):
        op.create_index(name, "gpu_scheduling_requests", columns)
    op.create_table(
        "gpu_leases",
        sa.Column("lease_token", sa.String(64), primary_key=True),
        sa.Column("request_id", sa.String(255), sa.ForeignKey("gpu_scheduling_requests.request_id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", sa.String(255), nullable=False),
        sa.Column("run_id", sa.String(255), nullable=False),
        sa.Column("step_id", sa.String(255), nullable=False),
        sa.Column("worker_id", sa.String(255), nullable=False),
        sa.Column("allocated_gpu_ids_json", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True)),
    )
    for column in ("request_id", "job_id", "run_id", "step_id", "worker_id", "status", "expires_at"):
        op.create_index(f"ix_gpu_leases_{column}", "gpu_leases", [column])


def downgrade() -> None:
    op.drop_table("gpu_leases")
    op.drop_table("gpu_scheduling_requests")
    op.drop_table("gpu_devices")
