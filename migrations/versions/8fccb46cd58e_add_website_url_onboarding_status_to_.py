"""add website_url and onboarding_status to tenants

Revision ID: 8fccb46cd58e
Revises: 5eb82a47f1b3
Create Date: 2026-06-08

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8fccb46cd58e"
down_revision: str | None = "5eb82a47f1b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("website_url", sa.String(), nullable=False, server_default=""),
    )
    op.alter_column("tenants", "website_url", server_default=None)
    op.add_column(
        "tenants",
        sa.Column(
            "onboarding_status", sa.String(), nullable=False, server_default="PENDING"
        ),
    )


def downgrade() -> None:
    op.drop_column("tenants", "onboarding_status")
    op.drop_column("tenants", "website_url")
