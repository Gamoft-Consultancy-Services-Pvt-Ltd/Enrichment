"""merge pan-kyb and lead-ingestion migration heads

Revision ID: f6e5d4c3b2a1
Revises: 70e8a8c1f36f, a1b2c3d4e5f6
Create Date: 2026-07-04

Both parents branch from 8fccb46cd58e: the lead-ingestion chain (tip
70e8a8c1f36f) and the PAN/KYB migration (a1b2c3d4e5f6). This is a no-op merge
that joins them into a single head so `alembic upgrade head` resolves.

"""

from collections.abc import Sequence

revision: str = "f6e5d4c3b2a1"
down_revision: str | Sequence[str] | None = ("70e8a8c1f36f", "a1b2c3d4e5f6")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
