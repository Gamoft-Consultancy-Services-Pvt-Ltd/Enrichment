"""Deduplication: find an existing Lead for an inbound event.

Match order: phone (E.164-normalised) → email (lowercased) → full_name + location.
Phone is stored normalised on the Lead so lookups remain consistent.
"""

import re
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from modules.lead_ingestion.db import repository
from modules.lead_ingestion.db.models import Lead, LeadTouchpoint
from modules.lead_ingestion.schemas.normalised_event import NormalisedChannelEvent


def normalise_phone(phone: str) -> str:
    """Strip formatting; keep digits and a leading + sign."""
    stripped = re.sub(r"[^\d+]", "", phone)
    if stripped.startswith("+"):
        return "+" + stripped[1:].replace("+", "")
    return stripped


async def find_duplicate(
    session: AsyncSession,
    tenant_id: UUID,
    event: NormalisedChannelEvent,
) -> Lead | None:
    """Return an existing Lead matching this event's identity, or None."""
    if event.phone:
        lead = await repository.get_lead_by_phone(session, tenant_id, normalise_phone(event.phone))
        if lead is not None:
            return lead

    if event.email:
        lead = await repository.get_lead_by_email(session, tenant_id, event.email.lower())
        if lead is not None:
            return lead

    if event.full_name and event.location:
        lead = await repository.get_lead_by_name_and_location(
            session, tenant_id, event.full_name, event.location
        )
        if lead is not None:
            return lead

    return None


def build_touchpoint(existing_lead: Lead, event: NormalisedChannelEvent) -> LeadTouchpoint:
    return LeadTouchpoint(
        lead_id=existing_lead.id,
        source_channel=event.source.value,
        raw_event_json=event.raw_event_json,
    )
