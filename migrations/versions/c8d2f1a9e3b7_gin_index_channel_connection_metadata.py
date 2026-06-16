"""gin_index_channel_connection_metadata

Revision ID: c8d2f1a9e3b7
Revises: 4f55595dc4ff
Create Date: 2026-06-13 00:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8d2f1a9e3b7"
down_revision: str | None = "4f55595dc4ff"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # GIN index on the metadata JSONB column makes tenant lookup by page_id /
    # ig_account_id / phone_number_id efficient on large connection tables.
    op.create_index(
        "ix_channel_connections_metadata_gin",
        "channel_connections",
        ["metadata"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_channel_connections_metadata_gin", table_name="channel_connections")
