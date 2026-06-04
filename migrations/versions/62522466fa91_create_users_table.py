"""create users table

Revision ID: 62522466fa91
Revises: 7c984ef0d9e8
Create Date: 2026-06-04 10:49:48.177609

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '62522466fa91'
down_revision: str | None = '7c984ef0d9e8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("auth0_sub", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("role", sa.Enum("PLATFORM_ADMIN", "TENANT", name="user_role"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.UniqueConstraint("auth0_sub"),
        sa.UniqueConstraint("tenant_id"),
    )


def downgrade() -> None:
    op.drop_table("users")
