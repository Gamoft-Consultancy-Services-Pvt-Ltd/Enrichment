"""add pan and kyb columns to tenants

Revision ID: a1b2c3d4e5f6
Revises: 8fccb46cd58e
Create Date: 2026-06-19

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "8fccb46cd58e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("pan", sa.String(), nullable=False, server_default=""))
    op.alter_column("tenants", "pan", server_default=None)
    op.add_column(
        "tenants",
        sa.Column("kyb_status", sa.String(), nullable=False, server_default="PENDING"),
    )
    op.add_column("tenants", sa.Column("kyb_company_data", postgresql.JSONB(), nullable=True))
    op.add_column(
        "tenants",
        sa.Column("kyb_verified_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenants", "kyb_verified_at")
    op.drop_column("tenants", "kyb_company_data")
    op.drop_column("tenants", "kyb_status")
    op.drop_column("tenants", "pan")
