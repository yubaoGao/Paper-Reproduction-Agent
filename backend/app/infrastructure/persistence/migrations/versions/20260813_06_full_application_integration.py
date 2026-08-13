"""Persist trusted repository snapshot registrations for worker recovery.

Revision ID: 20260813_06
Revises: 20260813_05
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260813_06"
down_revision = "20260813_05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "repository_snapshot_registrations",
        sa.Column("snapshot_id", sa.String(255), primary_key=True),
        sa.Column("repository_id", sa.String(255), nullable=False),
        sa.Column("resolved_commit_sha", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("host_path", sa.Text(), nullable=False),
        sa.Column("snapshot_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_repository_snapshot_registrations_repository_id",
        "repository_snapshot_registrations",
        ["repository_id"],
    )
    op.create_index(
        "ix_repository_snapshot_registrations_resolved_commit_sha",
        "repository_snapshot_registrations",
        ["resolved_commit_sha"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_repository_snapshot_registrations_resolved_commit_sha",
        table_name="repository_snapshot_registrations",
    )
    op.drop_index(
        "ix_repository_snapshot_registrations_repository_id",
        table_name="repository_snapshot_registrations",
    )
    op.drop_table("repository_snapshot_registrations")
