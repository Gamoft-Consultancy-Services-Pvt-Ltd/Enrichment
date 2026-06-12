"""Orchestrator: NormalisedChannelEvent → (Lead, LeadReceived | None).

Sprint 2 path (file upload, no LLM filter):
  redelivery-guard → dedup → create-lead → finalise-log → commit → LeadReceived

Sprint 3 path (message-based sources):
  filter → [terminal branch: reserve-slot → create-lead → finalise-log → commit]
         → OR → preflight → run_capture (reserve-slot → dedup → create-lead → commit)

Every path — including terminal filter branches and pre-flight blocks — claims a
platform_event_id slot via reserve_event_slot so that Meta webhook redeliveries
are a true no-op.  The LEAD / UNCLEAR path delegates to run_capture, which
handles its own reserve call; do NOT call reserve before delegating.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from modules.lead_ingestion import deduplicator, intake_logger
from modules.lead_ingestion.db import repository
from modules.lead_ingestion.db.models import Lead
from modules.lead_ingestion.deduplicator import normalise_phone
from modules.lead_ingestion.exceptions import PreFlightHaltError
from modules.lead_ingestion.pre_flight import check_pre_flight
from modules.lead_ingestion.schemas.filter_result import FilterClassification
from modules.lead_ingestion.schemas.normalised_event import NormalisedChannelEvent
from modules.lead_ingestion.two_stage_filter import run_filter
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


async def _create_terminal_lead(
    session: AsyncSession,
    event: NormalisedChannelEvent,
    pipeline_stage: str,
    *,
    block_reason: str | None = None,
) -> tuple[Lead, None]:
    """Create a terminal lead row with idempotency guard.

    Uses reserve_event_slot so that webhook redeliveries of a noise/blocked
    message are a no-op — identical to how run_capture handles the LEAD path.
    """
    log_id = await intake_logger.reserve_event_slot(session, event)
    if log_id is None:
        existing = await repository.get_lead_by_platform_event_id(
            session, event.platform_event_id
        )
        assert existing is not None, (
            f"IntakeEventLog exists for {event.platform_event_id!r} but no linked lead"
        )
        return (existing, None)

    lead = Lead(
        tenant_id=event.tenant_id,
        channel_connection_id=event.channel_connection_id,
        pipeline_stage=pipeline_stage,
        source_channel=event.source.value,
        phone=normalise_phone(event.phone) if event.phone else None,
        raw_event_json=event.raw_event_json,
        pre_flight_block_reason=block_reason,
    )
    session.add(lead)
    await session.flush()
    await intake_logger.finalise_event_log(session, log_id, lead.id, pipeline_stage)
    await session.commit()
    return (lead, None)


async def run_capture_message(
    session: AsyncSession,
    event: NormalisedChannelEvent,
) -> tuple[Lead, LeadReceived | None]:
    """Run the full pipeline for one message-based inbound event (WhatsApp, etc.).

    Adds the two-stage filter before the capture path.  File upload rows must
    use run_capture() instead — they bypass the filter entirely.

    Terminal states that return (lead, None):
      - NOISE             → pipeline_stage='insufficient_signal'
      - EXISTING_CUSTOMER → pipeline_stage='existing_customer'
      - pre-flight halt   → pipeline_stage='pre_flight_blocked'
      - duplicate         → existing lead, no new LeadReceived

    UNCLEAR is routed as LEAD per CLAUDE.md calibration rule.
    """
    # 1. Two-stage filter (confidence < 0.7 already escalated to LEAD inside run_filter)
    filter_result = await run_filter(event.raw_text or "")

    # 2. Terminal filter branches
    if filter_result.classification == FilterClassification.NOISE:
        return await _create_terminal_lead(session, event, "insufficient_signal")

    if filter_result.classification == FilterClassification.EXISTING_CUSTOMER:
        return await _create_terminal_lead(session, event, "existing_customer")

    # LEAD or UNCLEAR: calibration rule (CLAUDE.md) routes UNCLEAR as LEAD.
    # Merge any fields the LLM extracted from the message text.
    extracted = filter_result.extracted_fields
    enriched = NormalisedChannelEvent(
        event_id=event.event_id,
        tenant_id=event.tenant_id,
        channel_connection_id=event.channel_connection_id,
        source=event.source,
        platform_event_id=event.platform_event_id,
        full_name=event.full_name or extracted.get("name"),
        phone=event.phone or extracted.get("phone"),
        email=event.email or extracted.get("email"),
        location=event.location or extracted.get("location"),
        raw_text=event.raw_text,
        raw_event_json=event.raw_event_json,
        extra_fields=event.extra_fields,
        received_at=event.received_at,
    )

    # 3. Pre-flight
    try:
        await check_pre_flight(session, enriched.tenant_id)
    except PreFlightHaltError as exc:
        return await _create_terminal_lead(
            session, enriched, "pre_flight_blocked", block_reason=str(exc)
        )

    # 4. Delegate to run_capture (handles its own reserve_slot + dedup)
    return await run_capture(session, enriched)
