"""Public event schemas — the typed vocabulary modules use to communicate.

These are contracts only: the Event envelope and the concrete event types.
There is deliberately no publish/subscribe/bus here; delivery lands with the
first consumer (see ADR 0001). Other code imports these types directly, e.g.
`from shared.events.schemas import TenantActivated`.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class LeadSource(StrEnum):
    """All sources lead_ingestion accepts leads from."""

    GOOGLE_SHEETS = "GOOGLE_SHEETS"
    EMAIL = "EMAIL"
    WHATSAPP = "WHATSAPP"
    INSTAGRAM = "INSTAGRAM"
    FACEBOOK = "FACEBOOK"
    FACEBOOK_LEAD_ADS = "FACEBOOK_LEAD_ADS"
    FILE_UPLOAD = "FILE_UPLOAD"


class LeadBucket(StrEnum):
    """The scoring outcome bucket for a lead."""

    HOT = "HOT"
    WARM = "WARM"
    COLD = "COLD"


class Event(BaseModel):
    """Common envelope every event inherits. Immutable and tenant-scoped.

    Subclass this to define a concrete event; never instantiate Event directly.
    """

    model_config = ConfigDict(frozen=True)

    event_id: UUID = Field(default_factory=uuid4)
    event_type: str
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    tenant_id: UUID


class TenantActivated(Event):
    """A tenant transitioned to ACTIVE; held leads may now drain (ADR 0001)."""

    event_type: Literal["TenantActivated"] = "TenantActivated"


class LeadReceived(Event):
    """lead_ingestion accepted a genuine lead and persisted it; not yet scored."""

    event_type: Literal["LeadReceived"] = "LeadReceived"
    lead_id: UUID
    source: LeadSource


class LeadEnriched(Event):
    """enrichment finished gathering external data for a lead; ready to score."""

    event_type: Literal["LeadEnriched"] = "LeadEnriched"
    lead_id: UUID


class LeadScored(Event):
    """scoring produced a final composite score and bucket for a lead."""

    event_type: Literal["LeadScored"] = "LeadScored"
    lead_id: UUID
    score: float = Field(ge=0, le=100)
    bucket: LeadBucket


class LeadErasureRequested(Event):
    """COMP-303: a right-to-erasure request was fulfilled for one or more leads."""

    event_type: Literal["LeadErasureRequested"] = "LeadErasureRequested"
    erased_lead_ids: list[UUID]
    identifier_type: str
    identifier_hash: str
