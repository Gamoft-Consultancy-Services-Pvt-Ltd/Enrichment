"""Integration test: Facebook Lead Ads leadgen event → Lead(pipeline_stage='captured').

Calls run_lead_ad_capture() directly against a real DB session (no HTTP layer).
The Meta Graph API call is patched — no real network calls made.
Verifies:
  - Lead row with pipeline_stage='captured' and source_channel='FACEBOOK_LEAD_ADS'
  - Full name, email, phone correctly mapped from field_data
  - Extra fields (company_name) stored in extra_fields
  - IntakeEventLog row written with status='received'
  - platform_event_id = 'leadgen-{leadgen_id}'
  - LeadReceived event returned with correct source
  - Redelivery of the same leadgen_id is a no-op (idempotent)
"""

import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from modules.lead_ingestion.crypto import encrypt_credentials
from modules.lead_ingestion.db.models import IntakeEventLog, Lead
from modules.lead_ingestion.normaliser import normalise_lead_ad_form
from modules.lead_ingestion.pipeline import run_capture
from shared.channels.models import ChannelConnection
from shared.events.schemas import LeadSource
from shared.tenant import service as tenant_service
from shared.tenant.schemas import BusinessType, TenantCreate
from shared.tenant_config import service as config_service
from shared.tenant_config.schemas import TenantConfigCreate

_FIXTURE = Path(__file__).parents[2] / "fixtures" / "lead_ingestion" / "fb_leadgen_01.json"

_FIELD_DATA: list[dict[str, Any]] = [
    {"name": "full_name", "values": ["Ananya Kapoor"]},
    {"name": "email", "values": ["Ananya@Example.Com"]},
    {"name": "phone_number", "values": ["+91 98765 43210"]},
    {"name": "city", "values": ["Pune"]},
    {"name": "company_name", "values": ["Kapoor Builders"]},
]


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
            "thresholds": {"hot": 80.0, "warm": 55.0},
        }
    )


async def _make_tenant_with_config(session: AsyncSession) -> uuid.UUID:
    tenant = await tenant_service.create_tenant(
        session,
        TenantCreate(
            company_name="Lead Ads Co",
            primary_contact_name="Test User",
            primary_contact_email=f"test_{uuid.uuid4().hex[:8]}@example.com",
            business_type=BusinessType.B2B,
            website_url="https://example.com",  # type: ignore[arg-type]
        ),
    )
    await config_service.create_active(session, tenant.id, _config_payload())
    return tenant.id


async def _make_facebook_connection(
    session: AsyncSession, tenant_id: uuid.UUID, page_id: str
) -> ChannelConnection:
    from core.config import get_settings

    settings = get_settings()
    credentials = encrypt_credentials(
        {"page_access_token": "test-page-token", "page_id": page_id},
        key=settings.channel_credentials_encryption_key,
    )
    conn = ChannelConnection(
        tenant_id=tenant_id,
        channel_type="facebook",
        status="active",
        credentials_encrypted=credentials,
        connection_metadata={"page_id": page_id, "page_name": "Test Page"},
    )
    session.add(conn)
    await session.commit()
    await session.refresh(conn)
    return conn


async def test_lead_ad_captured_with_correct_fields(session: AsyncSession) -> None:
    tenant_id = await _make_tenant_with_config(session)
    conn = await _make_facebook_connection(session, tenant_id, "page-golden")
    leadgen_id = f"lg-{uuid.uuid4().hex[:8]}"

    event = normalise_lead_ad_form(
        _FIELD_DATA,
        leadgen_id=leadgen_id,
        tenant_id=tenant_id,
        channel_connection_id=conn.id,
    )
    lead, received = await run_capture(session, event)

    assert lead.pipeline_stage == "captured"
    assert lead.source_channel == LeadSource.FACEBOOK_LEAD_ADS.value
    assert lead.full_name == "Ananya Kapoor"
    assert lead.email == "ananya@example.com"
    assert lead.phone is not None
    assert "9876543210" in lead.phone
    assert lead.tenant_id == tenant_id
    assert received is not None
    assert received.source == LeadSource.FACEBOOK_LEAD_ADS


async def test_lead_ad_location_stored(session: AsyncSession) -> None:
    tenant_id = await _make_tenant_with_config(session)
    conn = await _make_facebook_connection(session, tenant_id, "page-location")
    leadgen_id = f"lg-{uuid.uuid4().hex[:8]}"

    event = normalise_lead_ad_form(
        _FIELD_DATA,
        leadgen_id=leadgen_id,
        tenant_id=tenant_id,
        channel_connection_id=conn.id,
    )
    lead, _ = await run_capture(session, event)
    assert lead.location is not None
    assert "Pune" in lead.location


async def test_lead_ad_extra_fields_stored(session: AsyncSession) -> None:
    tenant_id = await _make_tenant_with_config(session)
    conn = await _make_facebook_connection(session, tenant_id, "page-extra")
    leadgen_id = f"lg-{uuid.uuid4().hex[:8]}"

    event = normalise_lead_ad_form(
        _FIELD_DATA,
        leadgen_id=leadgen_id,
        tenant_id=tenant_id,
        channel_connection_id=conn.id,
    )
    lead, _ = await run_capture(session, event)
    assert lead.extra_fields is not None
    assert lead.extra_fields["company_name"] == "Kapoor Builders"


async def test_lead_ad_intake_log_written(session: AsyncSession) -> None:
    tenant_id = await _make_tenant_with_config(session)
    conn = await _make_facebook_connection(session, tenant_id, "page-log")
    leadgen_id = f"lg-{uuid.uuid4().hex[:8]}"

    event = normalise_lead_ad_form(
        _FIELD_DATA,
        leadgen_id=leadgen_id,
        tenant_id=tenant_id,
        channel_connection_id=conn.id,
    )
    lead, _ = await run_capture(session, event)

    log = (
        await session.execute(
            select(IntakeEventLog).where(
                IntakeEventLog.platform_event_id == f"leadgen-{leadgen_id}"
            )
        )
    ).scalar_one_or_none()

    assert log is not None
    assert log.status == "received"
    assert log.lead_id == lead.id
    assert log.source_channel == LeadSource.FACEBOOK_LEAD_ADS.value


async def test_lead_ad_redelivery_is_idempotent(session: AsyncSession) -> None:
    tenant_id = await _make_tenant_with_config(session)
    conn = await _make_facebook_connection(session, tenant_id, "page-idem")
    leadgen_id = f"lg-{uuid.uuid4().hex[:8]}"

    event = normalise_lead_ad_form(
        _FIELD_DATA,
        leadgen_id=leadgen_id,
        tenant_id=tenant_id,
        channel_connection_id=conn.id,
    )
    lead1, received1 = await run_capture(session, event)

    # Same leadgen_id → redelivery no-op
    event2 = normalise_lead_ad_form(
        _FIELD_DATA,
        leadgen_id=leadgen_id,
        tenant_id=tenant_id,
        channel_connection_id=conn.id,
    )
    lead2, received2 = await run_capture(session, event2)

    assert lead1.id == lead2.id
    assert received1 is not None
    assert received2 is None  # no duplicate LeadReceived

    leads = (
        await session.execute(select(Lead).where(Lead.tenant_id == tenant_id))
    ).scalars().all()
    assert len(leads) == 1
