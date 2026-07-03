"""COMP-302 — retention management: anonymise leads older than the configured window."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from modules.lead_ingestion.db.models import IntakeEventLog, Lead, LeadTouchpoint, RetentionEventLog

log = structlog.get_logger(__name__)


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _tombstone() -> dict[str, Any]:
    return {"anonymised": True, "anonymised_at": _now_utc().isoformat()}


async def get_leads_due_for_anonymisation(
    session: AsyncSession, *, retention_days: int
) -> Sequence[Lead]:
    """Return all leads whose created_at < now() - retention_days.

    Excludes already-anonymised and erased leads to avoid double-processing.
    """
    cutoff = _now_utc() - timedelta(days=retention_days)
    stmt = (
        select(Lead)
        .where(Lead.created_at < cutoff)
        .where(Lead.pipeline_stage.notin_(["anonymised", "erased"]))
    )
    result = await session.execute(stmt)
    return result.scalars().all()


async def get_event_logs_due_for_anonymisation(
    session: AsyncSession, *, retention_days: int
) -> Sequence[IntakeEventLog]:
    """Return IntakeEventLogs whose created_at < now() - retention_days."""
    cutoff = _now_utc() - timedelta(days=retention_days)
    stmt = select(IntakeEventLog).where(IntakeEventLog.created_at < cutoff)
    result = await session.execute(stmt)
    return result.scalars().all()


async def anonymise_lead(session: AsyncSession, lead: Lead, *, retention_days: int) -> None:
    """Null out PII columns, replace JSONB with tombstones, set stage, log audit row."""
    tombstone = _tombstone()

    lead.full_name = None
    lead.phone = None
    lead.email = None
    lead.location = None
    lead.raw_event_json = tombstone
    lead.extra_fields = None
    lead.pipeline_stage = "anonymised"

    tp_result = await session.execute(
        select(LeadTouchpoint).where(LeadTouchpoint.lead_id == lead.id)
    )
    for tp in tp_result.scalars().all():
        tp.raw_event_json = {"anonymised": True, "anonymised_at": _now_utc().isoformat()}

    session.add(
        RetentionEventLog(
            lead_id=lead.id,
            tenant_id=lead.tenant_id,
            action="anonymised",
            retention_days_applied=retention_days,
        )
    )
    log.info("lead_anonymised", lead_id=str(lead.id), retention_days=retention_days)


async def run_retention_sweep(session: AsyncSession) -> dict[str, int]:
    """Anonymise all overdue leads and event logs. Returns count summary."""
    settings = get_settings()

    leads = await get_leads_due_for_anonymisation(
        session, retention_days=settings.lead_data_retention_days
    )
    leads_anonymised = 0
    for lead in leads:
        await anonymise_lead(session, lead, retention_days=settings.lead_data_retention_days)
        leads_anonymised += 1

    event_logs = await get_event_logs_due_for_anonymisation(
        session, retention_days=settings.intake_log_retention_days
    )
    event_logs_redacted = 0
    for event_log in event_logs:
        event_log.raw_event_json = _tombstone()
        event_logs_redacted += 1

    await session.commit()

    log.info(
        "retention_sweep_complete",
        leads_anonymised=leads_anonymised,
        event_logs_redacted=event_logs_redacted,
    )
    return {"leads_anonymised": leads_anonymised, "event_logs_redacted": event_logs_redacted}
