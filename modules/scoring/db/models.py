"""
LEAD-47 — Scoring ORM models (Epic 6: Lead Scoring Runtime)

SQLAlchemy 2.0 typed models (Mapped / mapped_column) that inherit the
application-wide Base from core.db, so they register into the same metadata
and are picked up by Alembic alongside every other model.

Two tables:
- SignalSetRecord   : the active (and historical) scoring configuration per
                      tenant, stored as a JSONB payload (SignalSet.to_jsonb()).
- ScoringResultRecord: the persisted outcome of scoring one lead, with the
                      full breakdown as JSONB (ScoringResult.model_dump()).

Design choices:
- LEAD-47-S1: SignalSetRecord carries version, is_active, and source-pipeline
  provenance so configs are auditable across regenerations.
- LEAD-47-S3: "exactly one active SignalSet per tenant" is enforced at the DB
  level by a PARTIAL UNIQUE INDEX on (tenant_id) WHERE is_active — the database
  itself refuses a second active row. The repository (LEAD-48) additionally
  deactivates the prior version inside the activation transaction.
- LEAD-47-S4: indexes on the columns the repository and reporting layers filter
  by (tenant_id, lead_id, is_active, scored_at, classification).
- JSONB is used (not generic JSON) for Postgres; the type still degrades to
  JSON on SQLite in tests via the variant below.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from core.db import Base

# Use JSONB on Postgres, fall back to generic JSON elsewhere (e.g. SQLite tests).
JSONBType = JSONB().with_variant(JSON(), "sqlite")


class SignalSetRecord(Base):
    """Persisted scoring configuration for a tenant (one row per version)."""

    __tablename__ = "scoring_signal_sets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # LEAD-47-S1: the SignalSet serialized via SignalSet.to_jsonb()
    payload: Mapped[dict] = mapped_column(JSONBType, nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # source-pipeline provenance (which onboarding run produced this config)
    source_pipeline: Mapped[str | None] = mapped_column(String(128), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(), onupdate=func.now(),
    )

    __table_args__ = (
        # LEAD-47-S3: at most one active config per tenant, enforced by the DB.
        Index(
            "uq_signal_set_one_active_per_tenant",
            "tenant_id",
            unique=True,
            postgresql_where=(is_active.is_(True)),
            sqlite_where=(is_active.is_(True)),
        ),
        # LEAD-47-S4: lookup indexes
        Index("ix_signal_set_tenant", "tenant_id"),
        Index("ix_signal_set_tenant_version", "tenant_id", "version", unique=True),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"<SignalSetRecord tenant={self.tenant_id} v{self.version} "
            f"active={self.is_active}>"
        )


class ScoringResultRecord(Base):
    """Persisted outcome of scoring one lead."""

    __tablename__ = "scoring_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lead_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    signal_set_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    score: Mapped[float] = mapped_column(Float, nullable=False)
    classification: Mapped[str] = mapped_column(String(16), nullable=False)

    # LEAD-47-S2: full ScoringResult.model_dump(mode="json") breakdown
    breakdown: Mapped[dict] = mapped_column(JSONBType, nullable=False)

    llm_adjusted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # event-emission metadata (LEAD-57 worker marks results as emitted)
    event_emitted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    scored_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        # LEAD-47-S4: lookup indexes
        Index("ix_scoring_result_tenant", "tenant_id"),
        Index("ix_scoring_result_lead", "lead_id"),
        Index("ix_scoring_result_scored_at", "scored_at"),
        Index("ix_scoring_result_classification", "classification"),
        Index("ix_scoring_result_tenant_lead", "tenant_id", "lead_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"<ScoringResultRecord lead={self.lead_id} score={self.score} "
            f"{self.classification}>"
        )
