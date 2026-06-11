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

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.db import Base


class Lead(Base):
    """A de-duplicated contact record created when an inbound event passes pre-flight."""

    __tablename__ = "leads"

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
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id"), nullable=False, index=True
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
    """An additional inbound event that matched an existing Lead during dedup."""

    __tablename__ = "lead_touchpoints"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    lead_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("leads.id"), nullable=False, index=True
    )
    source_channel: Mapped[str] = mapped_column(String, nullable=False)
    raw_event_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
