"""Add persistent reproduction sessions and session-scoped job links.

Revision ID: 20260814_07
Revises: 20260813_06
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260814_07"
down_revision = "20260813_06"
branch_labels = None
depends_on = None


def upgrade() -> None:
    jsonb = postgresql.JSONB(astext_type=sa.Text())
    op.create_table(
        "reproduction_sessions",
        sa.Column("session_id", sa.String(255), primary_key=True),
        sa.Column("owner_principal", sa.String(255), nullable=False),
        sa.Column("origin_intake_id", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("repository_snapshot_id", sa.String(255), nullable=False),
        sa.Column("repository_commit_sha", sa.String(64), nullable=False),
        sa.Column("paper_content_hash", sa.String(64), nullable=False),
        sa.Column("session_json", jsonb, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("origin_intake_id", name="uq_reproduction_sessions_origin_intake_id"),
    )
    op.create_index(
        "ix_reproduction_sessions_owner_principal", "reproduction_sessions", ["owner_principal"],
    )
    op.create_index("ix_reproduction_sessions_status", "reproduction_sessions", ["status"])
    op.create_index(
        "ix_reproduction_sessions_repository_snapshot_id",
        "reproduction_sessions",
        ["repository_snapshot_id"],
    )
    op.create_index(
        "ix_reproduction_sessions_repository_commit_sha",
        "reproduction_sessions",
        ["repository_commit_sha"],
    )
    op.create_index("ix_reproduction_sessions_updated_at", "reproduction_sessions", ["updated_at"])

    op.add_column(
        "reproduction_jobs",
        sa.Column(
            "session_id",
            sa.String(255),
            sa.ForeignKey("reproduction_sessions.session_id", ondelete="SET NULL"),
        ),
    )
    op.create_index("ix_reproduction_jobs_session_id", "reproduction_jobs", ["session_id"])

    op.add_column(
        "reproduction_intakes",
        sa.Column(
            "session_id",
            sa.String(255),
            sa.ForeignKey("reproduction_sessions.session_id", ondelete="SET NULL"),
        ),
    )
    op.create_index("ix_reproduction_intakes_session_id", "reproduction_intakes", ["session_id"])

    op.add_column(
        "reproduction_events",
        sa.Column(
            "session_id",
            sa.String(255),
            sa.ForeignKey("reproduction_sessions.session_id", ondelete="SET NULL"),
        ),
    )
    op.create_index("ix_reproduction_events_session_id", "reproduction_events", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_reproduction_events_session_id", table_name="reproduction_events")
    op.drop_column("reproduction_events", "session_id")
    op.drop_index("ix_reproduction_intakes_session_id", table_name="reproduction_intakes")
    op.drop_column("reproduction_intakes", "session_id")
    op.drop_index("ix_reproduction_jobs_session_id", table_name="reproduction_jobs")
    op.drop_column("reproduction_jobs", "session_id")
    op.drop_index("ix_reproduction_sessions_updated_at", table_name="reproduction_sessions")
    op.drop_index(
        "ix_reproduction_sessions_repository_commit_sha", table_name="reproduction_sessions",
    )
    op.drop_index(
        "ix_reproduction_sessions_repository_snapshot_id", table_name="reproduction_sessions",
    )
    op.drop_index("ix_reproduction_sessions_status", table_name="reproduction_sessions")
    op.drop_index("ix_reproduction_sessions_owner_principal", table_name="reproduction_sessions")
    op.drop_table("reproduction_sessions")
