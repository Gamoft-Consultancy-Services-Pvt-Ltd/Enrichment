"""add scoring columns to leads

Revision ID: d1e2f3a4b5c6
Revises: c1d2e3f4a5b6
Create Date: 2026-07-19

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d1e2f3a4b5c6"
down_revision: str | None = "c1d2e3f4a5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("lead_bucket", sa.String(), nullable=True))
    op.add_column("leads", sa.Column("lead_score", sa.Float(), nullable=True))
    op.add_column("leads", sa.Column("scoring", postgresql.JSONB(), nullable=True))
    op.add_column("leads", sa.Column("scored_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("leads", "scored_at")
    op.drop_column("leads", "scoring")
    op.drop_column("leads", "lead_score")
    op.drop_column("leads", "lead_bucket")
