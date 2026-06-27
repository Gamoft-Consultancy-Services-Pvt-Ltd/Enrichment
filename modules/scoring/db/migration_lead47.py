"""create scoring tables (signal_sets, scoring_results)

Revision ID: lead47_scoring_tables
Revises: <SET_TO_YOUR_CURRENT_HEAD>
Create Date: 2026-06-12

LEAD-47-S5 — Epic 6 scoring persistence.

NOTE: set `down_revision` to your current Alembic head before running.
Find it with:  alembic heads
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "lead47_scoring_tables"
down_revision = None  # <-- REPLACE with your current head revision id
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- scoring_signal_sets ------------------------------------------------
    op.create_table(
        "scoring_signal_sets",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source_pipeline", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    # partial unique index: at most one active config per tenant (LEAD-47-S3)
    op.create_index(
        "uq_signal_set_one_active_per_tenant",
        "scoring_signal_sets",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )
    op.create_index("ix_signal_set_tenant", "scoring_signal_sets", ["tenant_id"])
    op.create_index("ix_signal_set_tenant_version", "scoring_signal_sets",
                    ["tenant_id", "version"], unique=True)

    # ---- scoring_results ----------------------------------------------------
    op.create_table(
        "scoring_results",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("lead_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("signal_set_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("classification", sa.String(length=16), nullable=False),
        sa.Column("breakdown", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("llm_adjusted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("event_emitted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("scored_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("ix_scoring_result_tenant", "scoring_results", ["tenant_id"])
    op.create_index("ix_scoring_result_lead", "scoring_results", ["lead_id"])
    op.create_index("ix_scoring_result_scored_at", "scoring_results", ["scored_at"])
    op.create_index("ix_scoring_result_classification", "scoring_results",
                    ["classification"])
    op.create_index("ix_scoring_result_tenant_lead", "scoring_results",
                    ["tenant_id", "lead_id"])


def downgrade() -> None:
    op.drop_index("ix_scoring_result_tenant_lead", table_name="scoring_results")
    op.drop_index("ix_scoring_result_classification", table_name="scoring_results")
    op.drop_index("ix_scoring_result_scored_at", table_name="scoring_results")
    op.drop_index("ix_scoring_result_lead", table_name="scoring_results")
    op.drop_index("ix_scoring_result_tenant", table_name="scoring_results")
    op.drop_table("scoring_results")

    op.drop_index("ix_signal_set_tenant_version", table_name="scoring_signal_sets")
    op.drop_index("ix_signal_set_tenant", table_name="scoring_signal_sets")
    op.drop_index("uq_signal_set_one_active_per_tenant",
                  table_name="scoring_signal_sets")
    op.drop_table("scoring_signal_sets")
