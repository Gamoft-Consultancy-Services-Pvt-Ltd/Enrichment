"""Enrichment service: run the research agent and attach Shopify order history.

Both business types run the research agent; only the goal differs. Order history
is fetched deterministically (never by the LLM) and only when Shopify creds are
present. The creds-populating wiring and the triggering event consumer are deferred.
"""

from modules.enrichment.schemas import (
    EnrichmentDeps,
    EnrichmentResult,
    ResearchFindings,
)
from modules.enrichment.tools.shopify_order_history import fetch_order_history
from shared.events.schemas import LeadPayload
from shared.research.agent import research
from shared.tenant.schemas import BusinessType, TenantRead


def _build_goal(lead: LeadPayload, business_type: BusinessType) -> str:
    """Compose the research goal, shaped by business type."""
    who = lead.company or lead.name or lead.email or lead.phone or "this lead"
    if business_type is BusinessType.B2B:
        return (
            f"Research the company associated with {who}. Find its industry, products "
            f"or services, size, geography, and any recent notable news. Assess how "
            f"strong a B2B prospect it is."
        )
    return (
        f"Research {who} for consumer context: the brand, product interests, and any "
        f"public signals relevant to a B2C purchase. Keep it light and factual."
    )


async def run_enrichment(
    lead: LeadPayload,
    tenant: TenantRead,
    deps: EnrichmentDeps | None = None,
) -> EnrichmentResult:
    """Enrich a lead: web research + (if Shopify-connected) order history."""
    goal = _build_goal(lead, tenant.business_type)
    findings: ResearchFindings = await research(goal=goal, output_schema=ResearchFindings)

    order_history = None
    if deps is not None and deps.shopify_creds is not None:
        order_history = await fetch_order_history(
            deps.shopify_creds, email=lead.email, phone=lead.phone
        )

    return EnrichmentResult(**findings.model_dump(), order_history=order_history)
