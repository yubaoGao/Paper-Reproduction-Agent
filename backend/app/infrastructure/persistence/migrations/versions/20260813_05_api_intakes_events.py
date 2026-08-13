"""Add owned API intakes and persistent product events.

Revision ID: 20260813_05
Revises: 20260813_04
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260813_05"
down_revision = "20260813_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    jsonb = postgresql.JSONB(astext_type=sa.Text())
    op.add_column(
        "reproduction_jobs",
        sa.Column("owner_principal", sa.String(255), nullable=False, server_default="system:legacy"),
    )
    op.create_index("ix_reproduction_jobs_owner_principal", "reproduction_jobs", ["owner_principal"])
    op.execute(
        "UPDATE reproduction_jobs SET job_json = "
        "jsonb_set(job_json, '{owner_principal}', to_jsonb(owner_principal), true)"
    )
    op.alter_column("reproduction_jobs", "owner_principal", server_default=None)

    op.create_table(
        "reproduction_intakes",
        sa.Column("intake_id", sa.String(255), primary_key=True),
        sa.Column("owner_principal", sa.String(255), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("job_id", sa.String(255), sa.ForeignKey("reproduction_jobs.job_id", ondelete="SET NULL"), unique=True),
        sa.Column("intake_json", jsonb, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_reproduction_intakes_owner_principal", "reproduction_intakes", ["owner_principal"])
    op.create_index("ix_reproduction_intakes_state", "reproduction_intakes", ["state"])
    op.create_index("ix_reproduction_intakes_job_id", "reproduction_intakes", ["job_id"])
    op.create_index("ix_reproduction_intakes_updated_at", "reproduction_intakes", ["updated_at"])

    op.create_table(
        "reproduction_events",
        sa.Column("sequence", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.String(255), nullable=False, unique=True),
        sa.Column("intake_id", sa.String(255), sa.ForeignKey("reproduction_intakes.intake_id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", sa.String(255), sa.ForeignKey("reproduction_jobs.job_id", ondelete="CASCADE")),
        sa.Column("owner_principal", sa.String(255), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload_json", jsonb, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_reproduction_events_owner_principal", "reproduction_events", ["owner_principal"])
    op.create_index("ix_reproduction_events_event_type", "reproduction_events", ["event_type"])
    op.create_index("ix_reproduction_events_created_at", "reproduction_events", ["created_at"])
    op.create_index("ix_reproduction_events_intake_sequence", "reproduction_events", ["intake_id", "sequence"])
    op.create_index("ix_reproduction_events_job_sequence", "reproduction_events", ["job_id", "sequence"])


def downgrade() -> None:
    op.drop_table("reproduction_events")
    op.drop_table("reproduction_intakes")
    op.execute("UPDATE reproduction_jobs SET job_json = job_json - 'owner_principal'")
    op.drop_index("ix_reproduction_jobs_owner_principal", table_name="reproduction_jobs")
    op.drop_column("reproduction_jobs", "owner_principal")
