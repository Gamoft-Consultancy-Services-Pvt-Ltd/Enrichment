"""Integration: run_lead_pipeline enriches a real lead row (research stubbed)."""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from core.config import get_settings
from modules.enrichment.schemas import ResearchFindings
from modules.lead_ingestion.service import Lead
from shared.events.schemas import LeadPayload, LeadReceived, LeadSource
from shared.tenant import service as tenant_service
from shared.tenant.schemas import BusinessType, TenantCreate
from workers.jobs.orchestration import run_lead_pipeline


async def _seed_tenant_and_lead(session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    tenant = await tenant_service.create_tenant(
        session,
        TenantCreate(
            company_name=f"Enrich Co {uuid.uuid4().hex[:6]}",
            primary_contact_name="Admin",
            primary_contact_email=f"admin_{uuid.uuid4().hex[:6]}@enrich.com",
            business_type=BusinessType.B2B,
            website_url="https://enrich.com",  # type: ignore[arg-type]
            pan="AAACX1234C",
            pan_holder_name="Test Holder Pvt Ltd",
            pan_dob="01/04/2019",
            consent=True,
        ),
    )
    lead = Lead(
        tenant_id=tenant.id,
        pipeline_stage="received",
        source_channel="EMAIL",
        full_name="Jane Doe",
        email="jane@fake.invalid",
        phone="+15550001234",
        raw_event_json={"demo": True},
        extra_fields={"company": "Stripe"},
    )
    session.add(lead)
    await session.commit()
    return tenant.id, lead.id


async def test_run_lead_pipeline_populates_enrichment(
    session: AsyncSession, monkeypatch: Any
) -> None:
    tenant_id, lead_id = await _seed_tenant_and_lead(session)

    from unittest.mock import AsyncMock

    findings = ResearchFindings(company_info={"industry": "SaaS"}, confidence=0.8)
    monkeypatch.setattr(
        "modules.enrichment.service.research", AsyncMock(return_value=findings)
    )

    event = LeadReceived(
        tenant_id=tenant_id,
        lead_id=lead_id,
        source=LeadSource.EMAIL,
        payload=LeadPayload(name="Jane Doe", company="Stripe", source=LeadSource.EMAIL),
    )

    engine = create_async_engine(get_settings().database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ctx: dict[str, object] = {"session_factory": factory}
    try:
        await run_lead_pipeline(ctx, event.model_dump(mode="json"))
    finally:
        await engine.dispose()

    session.expunge_all()
    fresh = await session.get(Lead, lead_id)
    assert fresh is not None
    assert fresh.enriched_at is not None
    assert fresh.enrichment is not None
    assert fresh.enrichment["company_info"] == {"industry": "SaaS"}
