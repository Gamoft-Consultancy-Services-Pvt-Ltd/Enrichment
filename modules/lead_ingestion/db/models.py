"""ORM models for the lead_ingestion module.

Four tables:
  leads                — one row per ingested contact; pipeline_stage tracks
                         where in the pipeline the lead currently sits.
  intake_event_logs    — append-only log of every raw inbound event; unique on
                         platform_event_id for idempotency (ON CONFLICT DO NOTHING).
  lead_form_field_maps — per-tenant header-alias overrides on top of STANDARD_FIELD_MAP.
  lead_touchpoints     — additional events linked to an existing lead (dedup hit).
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.db import Base


class Lead(Base):
    """A de-duplicated contact record created when an inbound event passes pre-flight."""

    __tablename__ = "leads"

    # Partial unique indexes on nullable identity columns — NULLs excluded so leads
    # without phone/email don't conflict. Guards against the read-then-write dedup
    # race: two concurrent webhooks for the same new contact can both pass
    # find_duplicate() before either commits; the DB constraint catches the second.
    __table_args__ = (
        Index(
            "uq_lead_tenant_phone",
            "tenant_id",
            "phone",
            unique=True,
            postgresql_where=text("phone IS NOT NULL"),
        ),
        Index(
            "uq_lead_tenant_email",
            "tenant_id",
            "email",
            unique=True,
            postgresql_where=text("email IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id"), nullable=False, index=True
    )
    channel_connection_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("channel_connections.id"), nullable=True, index=True
    )
    # String, not a PG ENUM — the value set grows across sprints and ALTERing a
    # PG enum in later migrations is painful (see onboarding_status precedent).
    pipeline_stage: Mapped[str] = mapped_column(String, nullable=False)
    source_channel: Mapped[str] = mapped_column(String, nullable=False)
    full_name: Mapped[str | None] = mapped_column(String, nullable=True)
    phone: Mapped[str | None] = mapped_column(String, nullable=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    location: Mapped[str | None] = mapped_column(String, nullable=True)
    raw_event_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    extra_fields: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    pre_flight_block_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    # Enrichment output (EnrichmentResult), attached after the lead is scored-ready.
    # NULL until enrichment runs. enriched_at records when it was last written.
    enrichment: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Scoring output, attached after enrichment. NULL until scoring runs, and
    # bucket/score stay NULL when no signal could be judged (trace still written).
    # lead_bucket is String, not a PG ENUM — same reasoning as pipeline_stage.
    lead_bucket: Mapped[str | None] = mapped_column(String, nullable=True)
    lead_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    scoring: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class IntakeEventLog(Base):
    """Append-only audit log of every raw inbound event, keyed by platform_event_id.

    The unique constraint on platform_event_id enables idempotent inserts via
    ON CONFLICT (platform_event_id) DO NOTHING.
    """

    __tablename__ = "intake_event_logs"

    __table_args__ = (
        UniqueConstraint("platform_event_id", name="uq_intake_event_log_platform_event_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("tenants.id"), nullable=True, index=True
    )
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("leads.id"), nullable=True, index=True
    )
    platform_event_id: Mapped[str] = mapped_column(String, nullable=False)
    source_channel: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    raw_event_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class LeadFormFieldMap(Base):
    """Per-tenant overrides mapping non-standard column headers to canonical fields.

    Takes precedence over STANDARD_FIELD_MAP for the owning tenant.
    Unique per (tenant_id, source_field_name) — a tenant maps each header once.
    """

    __tablename__ = "lead_form_field_maps"

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "source_field_name",
            name="uq_field_map_tenant_source_field",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id"), nullable=False, index=True
    )
    source_field_name: Mapped[str] = mapped_column(String, nullable=False)
    canonical_field_name: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class LeadTouchpoint(Base):
    """Every inbound event linked to a Lead — first capture and subsequent dedup hits."""

    __tablename__ = "lead_touchpoints"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    lead_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("leads.id"), nullable=False, index=True
    )
    platform_event_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    source_channel: Mapped[str] = mapped_column(String, nullable=False)
    raw_event_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RetentionEventLog(Base):
    """Audit log written once per anonymised lead (COMP-302 ST7)."""

    __tablename__ = "retention_event_logs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("leads.id"), nullable=True, index=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(String, nullable=False)
    performed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    retention_days_applied: Mapped[int] = mapped_column(Integer, nullable=False)


class ErasureEventLog(Base):
    """Audit log written once per right-to-erasure request (COMP-303)."""

    __tablename__ = "erasure_event_logs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id"), nullable=False, index=True
    )
    erased_lead_ids: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    identifier_type: Mapped[str] = mapped_column(String, nullable=False)
    identifier_hash: Mapped[str] = mapped_column(String, nullable=False)
    performed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    requested_by: Mapped[str] = mapped_column(String, nullable=False)
