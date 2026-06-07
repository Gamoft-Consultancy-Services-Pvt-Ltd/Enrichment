"""create tenant_configs table

Revision ID: 4ef9c33a98f6
Revises: 62522466fa91
Create Date: 2026-06-07 18:15:13.201204

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '4ef9c33a98f6'
down_revision: str | None = '62522466fa91'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenant_configs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("DRAFT", "ACTIVE", "ARCHIVED", "REJECTED", name="config_status"),
            nullable=False,
        ),
        sa.Column("business_profile", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("icp", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("signals", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("weights", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("thresholds", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.UniqueConstraint("tenant_id", "version", name="uq_tenant_config_version"),
    )
    op.create_index("ix_tenant_configs_tenant_id", "tenant_configs", ["tenant_id"])
    op.create_index(
        "uq_active_config_per_tenant",
        "tenant_configs",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.create_index(
        "uq_draft_config_per_tenant",
        "tenant_configs",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("status = 'DRAFT'"),
    )


def downgrade() -> None:
    op.drop_index("uq_draft_config_per_tenant", table_name="tenant_configs")
    op.drop_index("uq_active_config_per_tenant", table_name="tenant_configs")
    op.drop_index("ix_tenant_configs_tenant_id", table_name="tenant_configs")
    op.drop_table("tenant_configs")
    sa.Enum(name="config_status").drop(op.get_bind())
