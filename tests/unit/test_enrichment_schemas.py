"""Unit tests for modules/enrichment/schemas."""

import pytest
from pydantic import ValidationError

from modules.enrichment.schemas import (
    EnrichmentDeps,
    EnrichmentInput,
    EnrichmentResult,
    ResearchFindings,
    ShopifyCreds,
)
from shared.events.schemas import LeadPayload


def test_enrichment_input_is_lead_payload() -> None:
    assert EnrichmentInput is LeadPayload


def test_research_findings_defaults() -> None:
    f = ResearchFindings(confidence=0.5)
    assert f.company_info == {}
    assert f.sources == []


def test_research_findings_rejects_bad_confidence() -> None:
    with pytest.raises(ValidationError):
        ResearchFindings(confidence=2.0)


def test_findings_map_onto_enrichment_result() -> None:
    f = ResearchFindings(confidence=0.7, sources=["https://x"])
    result = EnrichmentResult(**f.model_dump(), order_history=None)
    assert result.confidence == 0.7
    assert result.order_history is None


def test_enrichment_deps_defaults_to_no_shopify() -> None:
    assert EnrichmentDeps().shopify_creds is None
    creds = ShopifyCreds(shop_domain="acme.myshopify.com", access_token="tok")
    assert EnrichmentDeps(shopify_creds=creds).shopify_creds is creds
