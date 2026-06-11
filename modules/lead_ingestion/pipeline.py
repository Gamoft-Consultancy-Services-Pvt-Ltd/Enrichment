"""Orchestrator: NormalisedChannelEvent → (Lead, LeadReceived | None).

Sprint 2 path (file upload, no LLM filter):
  redelivery-guard → dedup → create-lead → finalise-log → commit → LeadReceived

The redelivery guard (reserve_event_slot) runs BEFORE dedup so that a
re-submitted identical row is a true no-op: no new Lead, no new touchpoint.
The LLM filter stage is added in Sprint 3 for message-based sources.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from modules.lead_ingestion import deduplicator, intake_logger
from modules.lead_ingestion.db import repository
from modules.lead_ingestion.db.models import Lead
from modules.lead_ingestion.deduplicator import normalise_phone
from modules.lead_ingestion.schemas.normalised_event import NormalisedChannelEvent
from shared.events.schemas import LeadReceived


async def run_capture(
    session: AsyncSession,
    event: NormalisedChannelEvent,
) -> tuple[Lead, LeadReceived | None]:
    """Run the capture pipeline for one inbound event.

    Returns (lead, LeadReceived) on the happy path or (lead, None) for
    duplicates and redeliveries.  Commits internally once per event.
    """
    # 1. Redelivery guard: claim platform_event_id before running dedup.
    #    Placing this first means a re-submitted row never produces a
    #    spurious touchpoint — the second call short-circuits here.
    log_id = await intake_logger.reserve_event_slot(session, event)
    if log_id is None:
        # Same platform_event_id already processed — redelivery no-op.
        existing_lead = await repository.get_lead_by_platform_event_id(
            session, event.platform_event_id
        )
        assert existing_lead is not None, (
            f"IntakeEventLog exists for {event.platform_event_id!r} but no linked lead"
        )
        return (existing_lead, None)

    # 2. Dedup: same identity, different event_id → genuine new interaction.
    existing = await deduplicator.find_duplicate(session, event.tenant_id, event)
    if existing is not None:
        tp = deduplicator.build_touchpoint(existing, event)
        session.add(tp)
        await intake_logger.finalise_event_log(session, log_id, existing.id, "duplicate")
        await session.commit()
        return (existing, None)

    # 3. New lead.
    lead = Lead(
        tenant_id=event.tenant_id,
        channel_connection_id=event.channel_connection_id,
        pipeline_stage="captured",
        source_channel=event.source.value,
        full_name=event.full_name,
        phone=normalise_phone(event.phone) if event.phone else None,
        email=event.email.lower() if event.email else None,
        location=event.location,
        raw_event_json=event.raw_event_json,
        extra_fields=event.extra_fields if event.extra_fields else None,
    )
    session.add(lead)
    await session.flush()  # materialise lead.id for the foreign key below

    await intake_logger.finalise_event_log(session, log_id, lead.id, "received")
    await session.commit()

    return (
        lead,
        LeadReceived(
            tenant_id=event.tenant_id,
            lead_id=lead.id,
            source=event.source,
        ),
    )
