"""Add durable PostgreSQL job queue lease state.

Revision ID: 20260813_02
Revises: 20260813_01
"""

from alembic import op
import sqlalchemy as sa


revision = "20260813_02"
down_revision = "20260813_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("reproduction_jobs", sa.Column("enqueued_at", sa.DateTime(timezone=True)))
    op.add_column("reproduction_jobs", sa.Column("worker_id", sa.String(255)))
    op.add_column("reproduction_jobs", sa.Column("lease_token", sa.String(64)))
    op.add_column("reproduction_jobs", sa.Column("claimed_at", sa.DateTime(timezone=True)))
    op.add_column("reproduction_jobs", sa.Column("lease_expires_at", sa.DateTime(timezone=True)))
    op.add_column("reproduction_jobs", sa.Column("heartbeat_at", sa.DateTime(timezone=True)))
    op.add_column("reproduction_jobs", sa.Column("claim_count", sa.Integer(), server_default="0", nullable=False))
    op.add_column("reproduction_jobs", sa.Column("last_error", sa.Text()))
    op.create_index("ix_reproduction_jobs_worker_id", "reproduction_jobs", ["worker_id"])
    op.create_index("ix_reproduction_jobs_lease_expires_at", "reproduction_jobs", ["lease_expires_at"])
    op.create_index(
        "ix_reproduction_jobs_queue_order",
        "reproduction_jobs",
        ["status", "enqueued_at", "job_id"],
    )
    op.execute(
        "UPDATE reproduction_jobs "
        "SET status = 'queued', enqueued_at = COALESCE(updated_at, created_at) "
        "WHERE status = 'running'"
    )
    op.alter_column("reproduction_jobs", "claim_count", server_default=None)


def downgrade() -> None:
    op.execute(
        "UPDATE reproduction_jobs SET status = CASE "
        "WHEN status IN ('queued', 'claimed') THEN 'ready' "
        "WHEN status = 'cancel_requested' THEN 'cancelled' ELSE status END"
    )
    op.execute(
        "UPDATE reproduction_jobs SET job_json = jsonb_set("
        "job_json - 'enqueued_at' - 'worker_id' - 'lease_token' - 'claimed_at' "
        "- 'lease_expires_at' - 'heartbeat_at' - 'claim_count' - 'last_error', "
        "'{status}', to_jsonb(status::text), true)"
    )
    op.drop_index("ix_reproduction_jobs_queue_order", table_name="reproduction_jobs")
    op.drop_index("ix_reproduction_jobs_lease_expires_at", table_name="reproduction_jobs")
    op.drop_index("ix_reproduction_jobs_worker_id", table_name="reproduction_jobs")
    op.drop_column("reproduction_jobs", "last_error")
    op.drop_column("reproduction_jobs", "claim_count")
    op.drop_column("reproduction_jobs", "heartbeat_at")
    op.drop_column("reproduction_jobs", "lease_expires_at")
    op.drop_column("reproduction_jobs", "claimed_at")
    op.drop_column("reproduction_jobs", "lease_token")
    op.drop_column("reproduction_jobs", "worker_id")
    op.drop_column("reproduction_jobs", "enqueued_at")
