"""add_enrichment_columns_to_leads

Revision ID: c1d2e3f4a5b6
Revises: f6e5d4c3b2a1
Create Date: 2026-07-10 11:05:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c1d2e3f4a5b6'
down_revision: str | None = 'f6e5d4c3b2a1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('leads', sa.Column('enrichment', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('leads', sa.Column('enriched_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('leads', 'enriched_at')
    op.drop_column('leads', 'enrichment')
