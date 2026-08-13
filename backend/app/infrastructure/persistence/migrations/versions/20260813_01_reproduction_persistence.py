"""Create production reproduction persistence schema.

Revision ID: 20260813_01
Revises: None
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260813_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    jsonb = postgresql.JSONB(astext_type=sa.Text())
    op.create_table(
        "reproduction_jobs",
        sa.Column("job_id", sa.String(255), primary_key=True),
        sa.Column("paper_id", sa.String(255), nullable=False),
        sa.Column("paper_title", sa.Text(), nullable=False),
        sa.Column("user_goal", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("selection_json", jsonb, nullable=False),
        sa.Column("job_json", jsonb, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_reproduction_jobs_paper_id", "reproduction_jobs", ["paper_id"])
    op.create_index("ix_reproduction_jobs_status", "reproduction_jobs", ["status"])
    op.create_index("ix_reproduction_jobs_updated_at", "reproduction_jobs", ["updated_at"])

    op.create_table(
        "reproduction_planning_snapshots",
        sa.Column("snapshot_id", sa.String(255), primary_key=True),
        sa.Column("job_id", sa.String(255), sa.ForeignKey("reproduction_jobs.job_id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("specification_id", sa.String(255), nullable=False),
        sa.Column("plan_id", sa.String(255), nullable=False, unique=True),
        sa.Column("snapshot_json", jsonb, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_planning_snapshots_specification_id", "reproduction_planning_snapshots", ["specification_id"])

    op.create_table(
        "reproduction_runs",
        sa.Column("run_id", sa.String(255), primary_key=True),
        sa.Column("job_id", sa.String(255), sa.ForeignKey("reproduction_jobs.job_id", ondelete="CASCADE"), nullable=False),
        sa.Column("plan_id", sa.String(255), nullable=False),
        sa.Column("manifest_digest", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("run_json", jsonb, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_reproduction_runs_job_id", "reproduction_runs", ["job_id"])
    op.create_index("ix_reproduction_runs_plan_id", "reproduction_runs", ["plan_id"])
    op.create_index("ix_reproduction_runs_status", "reproduction_runs", ["status"])
    op.create_index("ix_reproduction_runs_updated_at", "reproduction_runs", ["updated_at"])

    op.create_table(
        "reproduction_step_runs",
        sa.Column("run_id", sa.String(255), sa.ForeignKey("reproduction_runs.run_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("step_id", sa.String(255), primary_key=True),
        sa.Column("experiment_id", sa.String(255), nullable=False),
        sa.Column("action_type", sa.String(64)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("step_json", jsonb, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_step_runs_experiment_id", "reproduction_step_runs", ["experiment_id"])
    op.create_index("ix_step_runs_status", "reproduction_step_runs", ["status"])

    op.create_table(
        "reproduction_attempt_records",
        sa.Column("run_id", sa.String(255), primary_key=True),
        sa.Column("step_id", sa.String(255), primary_key=True),
        sa.Column("attempt_number", sa.Integer(), primary_key=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("command_id", sa.String(255), nullable=False),
        sa.Column("attempt_json", jsonb, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id", "step_id"], ["reproduction_step_runs.run_id", "reproduction_step_runs.step_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("run_id", "step_id", "attempt_number", name="uq_attempt_owner_number"),
    )
    op.create_index("ix_attempt_records_status", "reproduction_attempt_records", ["status"])

    op.create_table(
        "reproduction_artifact_references",
        sa.Column("artifact_id", sa.String(64), primary_key=True),
        sa.Column("run_id", sa.String(255), sa.ForeignKey("reproduction_runs.run_id", ondelete="CASCADE"), nullable=False),
        sa.Column("step_id", sa.String(255)),
        sa.Column("attempt_number", sa.Integer()),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("uri", sa.Text(), nullable=False),
        sa.Column("checksum", sa.String(255)),
        sa.Column("size_bytes", sa.BigInteger()),
        sa.Column("metadata_json", jsonb, nullable=False),
        sa.ForeignKeyConstraint(["run_id", "step_id"], ["reproduction_step_runs.run_id", "reproduction_step_runs.step_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["run_id", "step_id", "attempt_number"],
            ["reproduction_attempt_records.run_id", "reproduction_attempt_records.step_id", "reproduction_attempt_records.attempt_number"],
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_artifact_references_run_id", "reproduction_artifact_references", ["run_id"])
    op.create_index("ix_artifact_references_step_id", "reproduction_artifact_references", ["step_id"])
    op.create_index("ix_artifact_references_kind", "reproduction_artifact_references", ["kind"])

    op.create_table(
        "reproduction_final_results",
        sa.Column("result_id", sa.String(255), primary_key=True),
        sa.Column("job_id", sa.String(255), sa.ForeignKey("reproduction_jobs.job_id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", sa.String(255), sa.ForeignKey("reproduction_runs.run_id", ondelete="CASCADE"), nullable=False),
        sa.Column("paper_experiment_id", sa.String(255), nullable=False),
        sa.Column("validation_status", sa.String(32), nullable=False),
        sa.Column("result_json", jsonb, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_final_results_job_id", "reproduction_final_results", ["job_id"])
    op.create_index("ix_final_results_run_id", "reproduction_final_results", ["run_id"])
    op.create_index("ix_final_results_paper_experiment_id", "reproduction_final_results", ["paper_experiment_id"])
    op.create_index("ix_final_results_validation_status", "reproduction_final_results", ["validation_status"])

    op.create_table(
        "reproduction_comparison_reports",
        sa.Column("report_id", sa.String(255), primary_key=True),
        sa.Column("job_id", sa.String(255), sa.ForeignKey("reproduction_jobs.job_id", ondelete="CASCADE"), nullable=False),
        sa.Column("report_json", jsonb, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_comparison_reports_job_id", "reproduction_comparison_reports", ["job_id"])
    op.create_index("ix_comparison_reports_updated_at", "reproduction_comparison_reports", ["updated_at"])


def downgrade() -> None:
    op.drop_table("reproduction_comparison_reports")
    op.drop_table("reproduction_final_results")
    op.drop_table("reproduction_artifact_references")
    op.drop_table("reproduction_attempt_records")
    op.drop_table("reproduction_step_runs")
    op.drop_table("reproduction_runs")
    op.drop_table("reproduction_planning_snapshots")
    op.drop_table("reproduction_jobs")
