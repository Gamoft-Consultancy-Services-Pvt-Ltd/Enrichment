"""COMP-303 — Right to Erasure: immediate PII removal on request."""

import hashlib
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from modules.lead_ingestion.db.models import ErasureEventLog, IntakeEventLog, Lead, LeadTouchpoint
from modules.lead_ingestion.deduplicator import normalise_phone
from shared.events.schemas import LeadErasureRequested

log = structlog.get_logger(__name__)


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _erased_tombstone() -> dict[str, Any]:
    return {"erased": True, "erased_at": _now_utc().isoformat()}


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


async def _find_leads(
    session: AsyncSession, tenant_id: UUID, *, phone: str | None, email: str | None
) -> list[Lead]:
    if phone is not None:
        result = await session.execute(
            select(Lead).where(Lead.tenant_id == tenant_id, Lead.phone == phone)
        )
    else:
        result = await session.execute(
            select(Lead).where(Lead.tenant_id == tenant_id, Lead.email == email)
        )
    return list(result.scalars().all())


async def _erase_one(session: AsyncSession, lead: Lead) -> None:
    tombstone = _erased_tombstone()
    lead.full_name = None
    lead.phone = None
    lead.email = None
    lead.location = None
    lead.raw_event_json = tombstone
    lead.extra_fields = None
    lead.pipeline_stage = "erased"

    tp_result = await session.execute(
        select(LeadTouchpoint).where(LeadTouchpoint.lead_id == lead.id)
    )
    for tp in tp_result.scalars().all():
        tp.raw_event_json = _erased_tombstone()

    iel_result = await session.execute(
        select(IntakeEventLog).where(IntakeEventLog.lead_id == lead.id)
    )
    for iel in iel_result.scalars().all():
        iel.raw_event_json = _erased_tombstone()


async def erase_lead_by_identity(
    session: AsyncSession,
    tenant_id: UUID,
    *,
    phone: str | None = None,
    email: str | None = None,
    requested_by: str,
) -> tuple[list[UUID], LeadErasureRequested | None]:
    """Find and erase all leads matching the given identity within the tenant.

    Normalises phone (E.164) and email (lowercase) before querying — matching the
    deduplicator's storage convention. Empty match → ([], None), no audit row written.
    ARQ in-flight safety: the platform_event_id idempotency guard short-circuits before
    any write, returning the already-erased lead rather than creating a new one.
    """
    if phone is None and email is None:
        return [], None

    identifier_type: str
    identifier_value: str

    if phone is not None:
        identifier_value = normalise_phone(phone)
        identifier_type = "phone"
        leads = await _find_leads(session, tenant_id, phone=identifier_value, email=None)
    else:
        assert email is not None
        identifier_value = email.lower()
        identifier_type = "email"
        leads = await _find_leads(session, tenant_id, phone=None, email=identifier_value)

    if not leads:
        return [], None

    for lead in leads:
        await _erase_one(session, lead)

    erased_ids = [lead.id for lead in leads]
    identifier_hash = _sha256(identifier_value)

    session.add(
        ErasureEventLog(
            tenant_id=tenant_id,
            erased_lead_ids=[str(lid) for lid in erased_ids],
            identifier_type=identifier_type,
            identifier_hash=identifier_hash,
            requested_by=requested_by,
        )
    )

    await session.commit()

    log.info(
        "erasure_complete",
        tenant_id=str(tenant_id),
        lead_count=len(erased_ids),
        identifier_type=identifier_type,
    )

    return erased_ids, LeadErasureRequested(
        tenant_id=tenant_id,
        erased_lead_ids=erased_ids,
        identifier_type=identifier_type,
        identifier_hash=identifier_hash,
    )
