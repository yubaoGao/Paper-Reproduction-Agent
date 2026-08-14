"""Add a dedicated intake analysis queue, separate from GPU reproduction jobs.

Revision ID: 20260815_08
Revises: 20260814_07
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260815_08"
down_revision = "20260814_07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    jsonb = postgresql.JSONB(astext_type=sa.Text())
    op.alter_column("reproduction_sessions", "repository_snapshot_id", existing_type=sa.String(255), nullable=True)
    op.alter_column("reproduction_sessions", "repository_commit_sha", existing_type=sa.String(64), nullable=True)
    op.create_table(
        "intake_analysis_jobs",
        sa.Column("job_id", sa.String(255), primary_key=True),
        sa.Column(
            "intake_id",
            sa.String(255),
            sa.ForeignKey("reproduction_intakes.intake_id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("owner_principal", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("paper_artifact_uri", sa.Text(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("worker_id", sa.String(255)),
        sa.Column("lease_token", sa.String(64)),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("enqueued_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("last_error_code", sa.String(64)),
        sa.Column("llm_call_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("job_json", jsonb, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_intake_analysis_jobs_intake_id", "intake_analysis_jobs", ["intake_id"], unique=True)
    op.create_index("ix_intake_analysis_jobs_owner_principal", "intake_analysis_jobs", ["owner_principal"])
    op.create_index("ix_intake_analysis_jobs_status", "intake_analysis_jobs", ["status"])
    op.create_index("ix_intake_analysis_jobs_lease_expires_at", "intake_analysis_jobs", ["lease_expires_at"])
    op.create_index("ix_intake_analysis_jobs_updated_at", "intake_analysis_jobs", ["updated_at"])
    op.create_index(
        "ix_intake_analysis_jobs_queue_order",
        "intake_analysis_jobs",
        ["status", "enqueued_at", "job_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_intake_analysis_jobs_queue_order", table_name="intake_analysis_jobs")
    op.drop_index("ix_intake_analysis_jobs_updated_at", table_name="intake_analysis_jobs")
    op.drop_index("ix_intake_analysis_jobs_lease_expires_at", table_name="intake_analysis_jobs")
    op.drop_index("ix_intake_analysis_jobs_status", table_name="intake_analysis_jobs")
    op.drop_index("ix_intake_analysis_jobs_owner_principal", table_name="intake_analysis_jobs")
    op.drop_index("ix_intake_analysis_jobs_intake_id", table_name="intake_analysis_jobs")
    op.drop_table("intake_analysis_jobs")
    op.alter_column("reproduction_sessions", "repository_commit_sha", existing_type=sa.String(64), nullable=False)
    op.alter_column("reproduction_sessions", "repository_snapshot_id", existing_type=sa.String(255), nullable=False)
