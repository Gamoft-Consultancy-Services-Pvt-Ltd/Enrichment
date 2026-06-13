"""Integration test: Facebook Lead Ad → Lead(pipeline_stage='captured').

Golden path assertions:
  - Lead row created with pipeline_stage='captured'
  - source_channel='facebook_lead_ad'
  - Identity fields (name, phone, email, location) carried from form data
  - IntakeEventLog row written with status='received'
  - LeadReceived event returned with correct tenant_id, lead_id, source
  - classify_message (Groq LLM) was NOT called — Lead Ads bypass the filter entirely
  - The leadgen_id is used as platform_event_id (idempotency key)

Setup:
  - Uses the real Postgres session from integration/conftest.py
  - Meta Graph API call (_fetch_lead_form_data) is patched to return lead_form_data_01.json
  - No actual network calls are made
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from modules.lead_ingestion.db.models import IntakeEventLog, Lead
from modules.lead_ingestion.lead_retrieval_worker import process_lead_ad_webhook
from shared.events.schemas import LeadSource
from shared.tenant import service as tenant_service
from shared.tenant.schemas import BusinessType, TenantCreate
from shared.tenant_config import service as config_service
from shared.tenant_config.schemas import TenantConfigCreate

_WEBHOOK_FIXTURE = (
    Path(__file__).parents[2] / "fixtures" / "lead_ingestion" / "lead_ad_webhook_01.json"
)
_FORM_DATA_FIXTURE = (
    Path(__file__).parents[2] / "fixtures" / "lead_ingestion" / "lead_form_data_01.json"
)


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


async def test_lead_ad_golden_path_zero_llm_calls(session: AsyncSession) -> None:
    """Lead Ad webhook → Lead row created, LLM filter never called."""
    # Arrange: create tenant + active config
    tenant = await tenant_service.create_tenant(
        session,
        TenantCreate(
            company_name="Lead Ad Golden Path Co",
            primary_contact_name="Aarav",
            primary_contact_email="aarav@leadad.com",
            business_type=BusinessType.B2C,
            website_url="https://leadad.com",  # type: ignore[arg-type]
        ),
    )
    await config_service.create_active(session, tenant.id, _config_payload())

    webhook_payload = json.loads(_WEBHOOK_FIXTURE.read_text(encoding="utf-8"))
    form_data = json.loads(_FORM_DATA_FIXTURE.read_text(encoding="utf-8"))

    # Act: patch the Meta Graph API call; LLM classify_message must NOT be called
    with (
        patch(
            "modules.lead_ingestion.lead_retrieval_worker._fetch_lead_form_data",
            new=AsyncMock(return_value=form_data),
        ),
        patch(
            "modules.lead_ingestion.two_stage_filter.classify_message",
            new=AsyncMock(side_effect=AssertionError("LLM must not be called for Lead Ads")),
        ) as mock_llm,
    ):
        lead, lr = await process_lead_ad_webhook(
            session,
            webhook_payload,
            tenant_id=tenant.id,
        )

    # Assert: LLM was never invoked
    mock_llm.assert_not_called()

    # Assert: Lead row
    assert lead.pipeline_stage == "captured"
    assert lead.source_channel == LeadSource.FACEBOOK_LEAD_AD.value
    assert lead.full_name == "Arjun Sharma"
    assert lead.phone == "+919988776655"
    assert lead.email == "arjun@example.com"
    assert lead.location == "Mumbai"
    assert lead.tenant_id == tenant.id

    # Assert: LeadReceived event
    assert lr is not None
    assert lr.lead_id == lead.id
    assert lr.tenant_id == tenant.id
    assert lr.source == LeadSource.FACEBOOK_LEAD_AD

    # Assert: IntakeEventLog row with the leadgen_id as platform_event_id
    log_row = (
        await session.execute(
            select(IntakeEventLog).where(
                IntakeEventLog.platform_event_id == "leadgen-555666777888999"
            )
        )
    ).scalar_one()
    assert log_row.status == "received"
    assert log_row.lead_id == lead.id


async def test_lead_ad_redelivery_is_noop(session: AsyncSession) -> None:
    """Redelivering the same Lead Ad webhook must not create a duplicate Lead."""
    tenant = await tenant_service.create_tenant(
        session,
        TenantCreate(
            company_name="Lead Ad Redelivery Co",
            primary_contact_name="Mia",
            primary_contact_email="mia@redelivery.com",
            business_type=BusinessType.B2B,
            website_url="https://redelivery.com",  # type: ignore[arg-type]
        ),
    )
    await config_service.create_active(session, tenant.id, _config_payload())

    webhook_payload = json.loads(_WEBHOOK_FIXTURE.read_text(encoding="utf-8"))
    form_data = json.loads(_FORM_DATA_FIXTURE.read_text(encoding="utf-8"))

    mock_fetch = AsyncMock(return_value=form_data)
    with patch(
        "modules.lead_ingestion.lead_retrieval_worker._fetch_lead_form_data",
        new=mock_fetch,
    ):
        lead_first, lr_first = await process_lead_ad_webhook(
            session, webhook_payload, tenant_id=tenant.id
        )
        lead_second, lr_second = await process_lead_ad_webhook(
            session, webhook_payload, tenant_id=tenant.id
        )

    # Same lead, no duplicate
    assert lead_first.id == lead_second.id
    assert lr_second is None

    all_leads = (
        (await session.execute(select(Lead).where(Lead.tenant_id == tenant.id))).scalars().all()
    )
    assert len(all_leads) == 1
