"""simplify config_status enum to ACTIVE and ARCHIVED only

Revision ID: 5eb82a47f1b3
Revises: 4ef9c33a98f6
Create Date: 2026-06-08

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5eb82a47f1b3"
down_revision: str | None = "4ef9c33a98f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Drop both partial indexes before touching the enum — they reference the
    # status column type and Postgres refuses to ALTER the column while they exist.
    op.drop_index("uq_draft_config_per_tenant", table_name="tenant_configs")
    op.drop_index("uq_active_config_per_tenant", table_name="tenant_configs")
    op.execute("ALTER TYPE config_status RENAME TO config_status_old")
    op.execute("CREATE TYPE config_status AS ENUM ('ACTIVE', 'ARCHIVED')")
    op.execute(
        "ALTER TABLE tenant_configs ALTER COLUMN status TYPE config_status "
        "USING status::text::config_status"
    )
    op.execute("DROP TYPE config_status_old")
    # Recreate only the ACTIVE partial index (DRAFT is gone).
    op.create_index(
        "uq_active_config_per_tenant",
        "tenant_configs",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )


def downgrade() -> None:
    op.drop_index("uq_active_config_per_tenant", table_name="tenant_configs")
    op.execute("ALTER TYPE config_status RENAME TO config_status_old")
    op.execute("CREATE TYPE config_status AS ENUM ('DRAFT', 'ACTIVE', 'ARCHIVED', 'REJECTED')")
    op.execute(
        "ALTER TABLE tenant_configs ALTER COLUMN status TYPE config_status "
        "USING status::text::config_status"
    )
    op.execute("DROP TYPE config_status_old")
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
