"""Integration tests — COMP-303 erasure idempotency: re-delivery after erasure returns the erased lead."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from modules.lead_ingestion.db.models import IntakeEventLog, Lead
from modules.lead_ingestion.erasure import erase_lead_by_identity
from modules.lead_ingestion.pipeline import run_capture
from modules.lead_ingestion.schemas.normalised_event import NormalisedChannelEvent
from shared.events.schemas import LeadSource
from shared.tenant import service as tenant_service
from shared.tenant.schemas import BusinessType, TenantCreate


@pytest.fixture
async def tenant_id(session: AsyncSession) -> uuid.UUID:
    tenant = await tenant_service.create_tenant(
        session,
        TenantCreate(
            company_name="Idempotency Test Co",
            primary_contact_name="Admin",
            primary_contact_email="admin@idemptest.com",
            business_type=BusinessType.B2C,
            website_url="https://idemptest.com",  # type: ignore[arg-type]
        ),
    )
    await session.commit()
    return tenant.id


async def test_redelivery_after_erasure_returns_erased_lead(
    session: AsyncSession, tenant_id: uuid.UUID
) -> None:
    """Re-delivering the same platform_event_id after erasure must short-circuit at
    reserve_event_slot and return the already-erased lead, not create a new one."""
    platform_event_id = f"evt-{uuid.uuid4()}"

    event = NormalisedChannelEvent(
        tenant_id=tenant_id,
        source=LeadSource.FILE_UPLOAD,
        platform_event_id=platform_event_id,
        full_name="Test User",
        phone="+919876543210",
        email="user@example.com",
        raw_event_json={"original": "event"},
        received_at=datetime.now(UTC),
    )

    # First capture — creates the lead
    original_lead, _ = await run_capture(session, event)
    assert original_lead.pipeline_stage == "captured"

    # Erase the lead
    erased_ids, _ = await erase_lead_by_identity(
        session, tenant_id, phone="+919876543210", requested_by="auth0|admin"
    )
    assert original_lead.id in erased_ids

    # IntakeEventLog rows for the erased lead must be tombstoned (highest-PII surface)
    intake_logs = (
        (
            await session.execute(
                select(IntakeEventLog).where(IntakeEventLog.lead_id == original_lead.id)
            )
        )
        .scalars()
        .all()
    )
    assert intake_logs, "run_capture must have written at least one IntakeEventLog"
    assert all(row.raw_event_json.get("erased") is True for row in intake_logs)

    # Re-deliver the exact same event (same platform_event_id)
    redelivered_lead, received_event = await run_capture(session, event)

    # Must return the same (erased) lead, not a new one
    assert redelivered_lead.id == original_lead.id
    assert received_event is None  # no new LeadReceived — guard fired

    # Lead must still be erased (not overwritten)
    lead_id = original_lead.id  # capture before expire_all to avoid lazy-load error
    session.expire_all()
    refreshed = await session.get(Lead, lead_id)
    assert refreshed is not None
    assert refreshed.pipeline_stage == "erased"
    assert refreshed.phone is None

    # No extra leads created
    all_leads = (
        (await session.execute(select(Lead).where(Lead.tenant_id == tenant_id))).scalars().all()
    )
    assert len(all_leads) == 1
