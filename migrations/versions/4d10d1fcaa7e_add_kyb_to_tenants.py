"""add kyb to tenants

Revision ID: 4d10d1fcaa7e
Revises: 8fccb46cd58e
Create Date: 2026-06-15 19:09:40.316688

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '4d10d1fcaa7e'
down_revision: str | None = '8fccb46cd58e'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("gstin", sa.String(), nullable=False, server_default=""),
    )
    op.alter_column("tenants", "gstin", server_default=None)
    op.add_column(
        "tenants",
        sa.Column("kyb_status", sa.String(), nullable=False, server_default="PENDING"),
    )
    op.add_column(
        "tenants",
        sa.Column("kyb_company_data", postgresql.JSONB(), nullable=True),
    )
    op.add_column("tenants", sa.Column("kyb_txn_ref", sa.String(), nullable=True))
    op.add_column(
        "tenants",
        sa.Column("kyb_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "tenants",
        sa.Column("kyb_resends", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "tenants",
        sa.Column("kyb_verified_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenants", "kyb_verified_at")
    op.drop_column("tenants", "kyb_resends")
    op.drop_column("tenants", "kyb_attempts")
    op.drop_column("tenants", "kyb_txn_ref")
    op.drop_column("tenants", "kyb_company_data")
    op.drop_column("tenants", "kyb_status")
    op.drop_column("tenants", "gstin")
