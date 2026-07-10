"""Unit tests for modules/enrichment/service — mocks research + shopify."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.enrichment import service
from modules.enrichment.schemas import EnrichmentDeps, ResearchFindings, ShopifyCreds
from shared.events.schemas import LeadPayload, LeadSource
from shared.tenant.schemas import BusinessType


def _tenant(business_type: BusinessType) -> MagicMock:
    t = MagicMock()
    t.business_type = business_type
    t.company_name = "Acme"
    return t


def _lead() -> LeadPayload:
    return LeadPayload(name="Priya", email="p@acme.com", phone="+91", source=LeadSource.EMAIL)


async def test_run_enrichment_without_shopify_has_no_order_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    findings = ResearchFindings(company_info={"industry": "SaaS"}, confidence=0.8)
    monkeypatch.setattr(service, "research", AsyncMock(return_value=findings))
    fetch = AsyncMock()
    monkeypatch.setattr(service, "fetch_order_history", fetch)

    result = await service.run_enrichment(_lead(), _tenant(BusinessType.B2B))

    assert result.company_info == {"industry": "SaaS"}
    assert result.order_history is None
    fetch.assert_not_called()


async def test_run_enrichment_with_shopify_attaches_order_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    findings = ResearchFindings(confidence=0.6)
    monkeypatch.setattr(service, "research", AsyncMock(return_value=findings))
    orders = {"customer_found": True, "order_count": 3}
    monkeypatch.setattr(service, "fetch_order_history", AsyncMock(return_value=orders))

    deps = EnrichmentDeps(shopify_creds=ShopifyCreds(shop_domain="s.myshopify.com", access_token="t"))
    result = await service.run_enrichment(_lead(), _tenant(BusinessType.B2C), deps)

    assert result.order_history == orders


async def test_goal_varies_by_business_type(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    async def fake_research(*, goal: str, output_schema: type) -> ResearchFindings:
        captured["goal"] = goal
        return ResearchFindings(confidence=0.5)

    monkeypatch.setattr(service, "research", fake_research)
    await service.run_enrichment(_lead(), _tenant(BusinessType.B2B))
    b2b_goal = captured["goal"]
    await service.run_enrichment(_lead(), _tenant(BusinessType.B2C))
    b2c_goal = captured["goal"]
    assert b2b_goal != b2c_goal
