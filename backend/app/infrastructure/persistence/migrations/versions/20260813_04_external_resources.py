"""Add external resource registry.

Revision ID: 20260813_04
Revises: 20260813_03
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260813_04"
down_revision = "20260813_03"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "external_resource_bindings",
        sa.Column("resource_id", sa.String(length=255), primary_key=True),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("canonical_key", sa.Text(), nullable=False),
        sa.Column("resource_type", sa.String(length=32), nullable=False),
        sa.Column("host_path", sa.Text(), nullable=False),
        sa.Column("access", sa.String(length=32), nullable=False),
        sa.Column("owner_principal", sa.String(length=255), nullable=True),
        sa.Column("shared", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("validation_status", sa.String(length=32), nullable=False),
        sa.Column("binding_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_external_resource_bindings_resource_type", "external_resource_bindings", ["resource_type"])
    op.create_index("ix_external_resource_bindings_owner_principal", "external_resource_bindings", ["owner_principal"])
    op.create_index("ix_external_resource_bindings_shared", "external_resource_bindings", ["shared"])
    op.create_index("ix_external_resource_bindings_validation_status", "external_resource_bindings", ["validation_status"])
    op.create_index("ix_external_resource_bindings_updated_at", "external_resource_bindings", ["updated_at"])
    op.create_index("ix_external_resource_identity", "external_resource_bindings", ["resource_type", "canonical_key", "owner_principal", "shared"])


def downgrade():
    op.drop_table("external_resource_bindings")
