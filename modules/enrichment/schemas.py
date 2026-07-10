"""Enrichment schemas: the agent's output, Shopify deps, and the shared contract.

ResearchFindings is the agent's response_format output. EnrichmentResult (the
LeadEnriched payload) is ResearchFindings plus the deterministically-fetched
order_history. Both the input (LeadPayload) and result are defined once in
shared/events; this module re-exports them so callers import from one place.
"""

from typing import Any

from pydantic import BaseModel, Field

from shared.events.schemas import EnrichmentResult, LeadPayload

EnrichmentInput = LeadPayload

__all__ = [
    "EnrichmentDeps",
    "EnrichmentInput",
    "EnrichmentResult",
    "ResearchFindings",
    "ShopifyCreds",
]


class ResearchFindings(BaseModel):
    """The web-research half of an enrichment — the agent's structured output."""

    company_info: dict[str, Any] = Field(default_factory=dict)
    person_info: dict[str, Any] = Field(default_factory=dict)
    sources: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    reasoning_trace: str = ""


class ShopifyCreds(BaseModel):
    """A tenant's Shopify Admin API credentials (never sent to the LLM)."""

    shop_domain: str
    access_token: str


class EnrichmentDeps(BaseModel):
    """Runtime dependencies injected per enrichment run by the (future) caller."""

    shopify_creds: ShopifyCreds | None = None
