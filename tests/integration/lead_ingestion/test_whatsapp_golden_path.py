"""Integration test: WhatsApp DM → Lead(pipeline_stage='captured').

Calls run_capture_message() directly against a real DB session (no HTTP layer).
The Groq client is patched to return a LEAD classification so no real LLM calls
are made.  The test verifies:
  - Lead row with pipeline_stage='captured'
  - Phone stored in E.164 format (+ prefix added by normaliser)
  - Full name carried from the contacts array
  - IntakeEventLog row written with status='received'
  - LeadReceived fields match the persisted Lead
  - classify_message called exactly once (Stage 1 passes, Stage 2 fires)
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
from shared.events.schemas import LeadSource
from shared.tenant import service as tenant_service
from shared.tenant.schemas import BusinessType, TenantCreate
from shared.tenant_config import service as config_service
from shared.tenant_config.schemas import TenantConfigCreate

_FIXTURE = Path(__file__).parents[2] / "fixtures" / "lead_ingestion" / "wa_dm_01.json"


def _config_payload() -> TenantConfigCreate:
    return TenantConfigCreate.model_validate(
        {
            "business_profile": {"summary": "Real estate leads"},
            "icp": {"summary": "Mid-market home buyers"},
            "signals": [
                {"id": "fit_1", "dimension": "FIT", "question": "In target city?"},
                {"id": "intent_1", "dimension": "INTENT", "question": "Actively searching?"},
                {"id": "eng_1", "dimension": "ENGAGEMENT", "question": "Replied to ad?"},
                {"id": "beh_1", "dimension": "BEHAVIOUR", "question": "Visited site?"},
                {"id": "ctx_1", "dimension": "CONTEXT", "question": "Recent life event?"},
            ],
            "weights": {
                "fit": 0.2,
                "intent": 0.2,
                "engagement": 0.2,
                "behaviour": 0.2,
                "context": 0.2,
            },
            "thresholds": {"hot": 80, "warm": 55},
        }
    )


async def _make_tenant_with_config(session: AsyncSession) -> UUID:
    tenant = await tenant_service.create_tenant(
        session,
        TenantCreate(
            company_name="WA Golden Path Co",
            primary_contact_name="Priya",
            primary_contact_email="priya@wagolden.com",
            business_type=BusinessType.B2C,
            website_url="https://wagolden.com",  # type: ignore[arg-type]
        ),
    )
    await config_service.create_active(session, tenant.id, _config_payload())
    return tenant.id


async def test_whatsapp_dm_lead_captured(session: AsyncSession) -> None:
    tenant_id = await _make_tenant_with_config(session)
    payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    event = normalise_whatsapp_message(payload, tenant_id=tenant_id)

    groq_response = {
        "classification": "LEAD",
        "extracted_fields": {"name": "Priya Mehta"},
        "confidence": 0.95,
    }
    with patch(
        "modules.lead_ingestion.two_stage_filter.classify_message",
        new=AsyncMock(return_value=groq_response),
    ) as mock_classify:
        lead, lr = await run_capture_message(session, event)

    # Lead persisted with correct stage and identity fields
    assert lead.pipeline_stage == "captured"
    assert lead.phone == "+919876543210"
    assert lead.full_name == "Priya Mehta"
    assert lead.source_channel == LeadSource.WHATSAPP.value
    assert lead.tenant_id == tenant_id

    # LeadReceived event published
    assert lr is not None
    assert lr.lead_id == lead.id
    assert lr.tenant_id == tenant_id
    assert lr.source == LeadSource.WHATSAPP

    # IntakeEventLog row written
    log_row = (
        await session.execute(
            select(IntakeEventLog).where(
                IntakeEventLog.platform_event_id == "wamid.sprint3golden001"
            )
        )
    ).scalar_one()
    assert log_row.status == "received"
    assert log_row.lead_id == lead.id

    # Stage 2 fired exactly once (message text passes Stage 1)
    mock_classify.assert_called_once()


async def test_whatsapp_dm_redelivery_is_noop(session: AsyncSession) -> None:
    """Redelivering the same webhook payload must not create a duplicate Lead."""
    tenant_id = await _make_tenant_with_config(session)
    payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    event = normalise_whatsapp_message(payload, tenant_id=tenant_id)

    groq_response = {"classification": "LEAD", "extracted_fields": {}, "confidence": 0.9}
    mock = AsyncMock(return_value=groq_response)
    with patch("modules.lead_ingestion.two_stage_filter.classify_message", new=mock):
        lead_first, lr_first = await run_capture_message(session, event)
        lead_second, lr_second = await run_capture_message(session, event)

    # Same lead returned; no duplicate created
    assert lead_first.id == lead_second.id
    assert lr_second is None

    count = (await session.execute(select(Lead).where(Lead.tenant_id == tenant_id))).scalars().all()
    assert len(count) == 1
