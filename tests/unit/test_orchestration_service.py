"""Unit tests for modules/orchestration/service.process_lead — all deps mocked."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from modules.orchestration import service
from shared.events.schemas import LeadPayload, LeadReceived, LeadSource
from shared.tenant.schemas import (
    BusinessType,
    KybStatus,
    OnboardingStatus,
    TenantRead,
    TenantStatus,
)


def _tenant_read() -> TenantRead:
    return TenantRead(
        id=uuid4(),
        company_name="Acme",
        primary_contact_name="Admin",
        primary_contact_email="admin@acme.com",
        business_type=BusinessType.B2B,
        website_url="https://acme.com",
        pan="ABCDE1234F",
        kyb_status=KybStatus.VERIFIED,
        onboarding_status=OnboardingStatus.COMPLETE,
        status=TenantStatus.ACTIVE,
        timezone="UTC",
        language_preference="en",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        activated_at=None,
    )


def _event() -> LeadReceived:
    return LeadReceived(
        tenant_id=uuid4(),
        lead_id=uuid4(),
        source=LeadSource.EMAIL,
        payload=LeadPayload(name="Priya", email="p@acme.com", source=LeadSource.EMAIL),
    )


async def test_process_lead_enriches_then_stores(monkeypatch: pytest.MonkeyPatch) -> None:
    event = _event()
    tenant = _tenant_read()
    result = MagicMock(name="EnrichmentResult")

    get_tenant = AsyncMock(return_value=tenant)
    run_enrichment = AsyncMock(return_value=result)
    store = AsyncMock()
    monkeypatch.setattr(service, "get_tenant", get_tenant)
    monkeypatch.setattr(service, "run_enrichment", run_enrichment)
    monkeypatch.setattr(service, "store_lead_enrichment", store)

    session = MagicMock(name="session")
    await service.process_lead(session, event)

    get_tenant.assert_awaited_once_with(session, event.tenant_id)
    # enrichment gets the event payload and a TenantRead carrying the tenant's business_type
    enrich_call = run_enrichment.await_args
    assert enrich_call is not None
    assert enrich_call.args[0] == event.payload
    passed_tenant = enrich_call.args[1]
    assert isinstance(passed_tenant, TenantRead)
    assert passed_tenant.business_type is BusinessType.B2B
    # result is persisted onto the lead row
    store.assert_awaited_once_with(session, event.lead_id, result)
