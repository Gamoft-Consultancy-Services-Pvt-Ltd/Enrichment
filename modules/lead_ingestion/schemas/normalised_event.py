"""NormalisedChannelEvent — the canonical in-process representation of any inbound event.

Every source adapter (WhatsApp, file upload, email, sheets) produces one of these
before the event enters the pipeline.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from shared.events.schemas import LeadSource


class NormalisedChannelEvent(BaseModel):
    """Immutable, source-agnostic representation of one inbound lead event."""

    model_config = ConfigDict(frozen=True)

    event_id: UUID = Field(default_factory=uuid4)
    tenant_id: UUID
    channel_connection_id: UUID | None = None
    source: LeadSource
    platform_event_id: str
    full_name: str | None = None
    phone: str | None = None
    email: str | None = None
    location: str | None = None
    raw_text: str | None = None
    raw_event_json: dict[str, Any]
    extra_fields: dict[str, Any] = Field(default_factory=dict)
    received_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
