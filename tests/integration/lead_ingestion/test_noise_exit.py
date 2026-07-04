"""Integration test: WhatsApp noise message → Lead(pipeline_stage='insufficient_signal').

Calls run_capture_message() directly against a real DB session.  The message
text is "👍" — caught by Stage 1 (emoji-only rule) — so classify_message must
NOT be called.  Verifies:
  - Lead row with pipeline_stage='insufficient_signal'
  - No LeadReceived returned
  - IntakeEventLog row written with status='insufficient_signal'
  - classify_message never called (Stage 1 short-circuit)
  - Redelivery of the same payload is a true no-op (no second Lead row)
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from modules.lead_ingestion.db.models import IntakeEventLog, Lead
from modules.lead_ingestion.normaliser import normalise_whatsapp_message
from modules.lead_ingestion.pipeline import run_capture_message
from shared.tenant import service as tenant_service
from shared.tenant.schemas import BusinessType, TenantCreate

_FIXTURE = Path(__file__).parents[2] / "fixtures" / "lead_ingestion" / "wa_noise_01.json"


async def _make_tenant(session: AsyncSession) -> UUID:
    # No TenantConfig needed — noise messages are discarded before pre-flight.
    tenant = await tenant_service.create_tenant(
        session,
        TenantCreate(
            company_name="WA Noise Co",
            primary_contact_name="Test",
            primary_contact_email="test@wanoise.com",
            business_type=BusinessType.B2C,
            website_url="https://wanoise.com",  # type: ignore[arg-type]
            pan="AAACX1234C",
            pan_holder_name="Test Holder Pvt Ltd",
            pan_dob="01/04/2019",
            consent=True,
        ),
    )
    return tenant.id


async def test_noise_message_produces_insufficient_signal_lead(session: AsyncSession) -> None:
    tenant_id = await _make_tenant(session)
    payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    event = normalise_whatsapp_message(payload, tenant_id=tenant_id)

    with patch(
        "modules.lead_ingestion.two_stage_filter.classify_message",
        new=AsyncMock(),
    ) as mock_classify:
        lead, lr = await run_capture_message(session, event)

    # Terminal stage — no LeadReceived
    assert lead.pipeline_stage == "insufficient_signal"
    assert lr is None

    # Stage 1 short-circuit — Groq must not be called
    mock_classify.assert_not_called()

    # IntakeEventLog row written for idempotency
    log_row = (
        await session.execute(
            select(IntakeEventLog).where(
                IntakeEventLog.platform_event_id == "wamid.sprint3noise001"
            )
        )
    ).scalar_one()
    assert log_row.status == "insufficient_signal"
    assert log_row.lead_id == lead.id


async def test_noise_redelivery_is_noop(session: AsyncSession) -> None:
    """Redelivering the same noise webhook must not create a second Lead row."""
    tenant_id = await _make_tenant(session)
    payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    event = normalise_whatsapp_message(payload, tenant_id=tenant_id)

    with patch("modules.lead_ingestion.two_stage_filter.classify_message", new=AsyncMock()):
        lead_first, _ = await run_capture_message(session, event)
        lead_second, lr_second = await run_capture_message(session, event)

    assert lead_first.id == lead_second.id
    assert lr_second is None

    all_leads = (
        (await session.execute(select(Lead).where(Lead.tenant_id == tenant_id))).scalars().all()
    )
    assert len(all_leads) == 1
