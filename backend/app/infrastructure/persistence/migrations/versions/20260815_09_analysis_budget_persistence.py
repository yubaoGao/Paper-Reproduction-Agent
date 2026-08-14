"""Persist analysis phase start time and lifetime LLM call counts.

Revision ID: 20260815_09
Revises: 20260815_08
"""

from alembic import op
import sqlalchemy as sa


revision = "20260815_09"
down_revision = "20260815_08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "intake_analysis_jobs",
        sa.Column("analysis_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "intake_analysis_jobs",
        sa.Column("lifetime_llm_call_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("intake_analysis_jobs", "lifetime_llm_call_count")
    op.drop_column("intake_analysis_jobs", "analysis_started_at")
