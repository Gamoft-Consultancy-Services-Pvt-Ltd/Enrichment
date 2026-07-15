"""Pipeline: research the tenant's company, then run the three agents in sequence."""

from typing import Any
from uuid import UUID

import structlog
from langfuse.decorators import langfuse_context, observe
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from modules.tenant_onboarding.agents import icp, persona, signals
from shared.research.agent import research
from shared.tenant.schemas import OnboardingStatus
from shared.tenant.service import activate_tenant, get_tenant, set_onboarding_status
from shared.tenant_config.schemas import TenantConfigCreate
from shared.tenant_config.service import create_active

log = structlog.get_logger(__name__)


class CompanyInfo(BaseModel):
    """Structured company facts gathered by the research agent for onboarding."""

    summary: str = ""
    industry: str = ""
    products_services: str = ""
    target_market: str = ""
    notable_facts: list[str] = Field(default_factory=list)


def _company_goal(website_url: str, company_name: str) -> str:
    """Compose the research goal for a tenant's own company."""
    return (
        f"Research the company '{company_name}' (website: {website_url}). Summarize what "
        f"it does, its industry, its products or services, its target market, and any "
        f"notable facts. Base everything only on what you find."
    )


@observe(capture_input=False)
async def run_pipeline(session: AsyncSession, tenant_id: UUID) -> None:
    """Run the full onboarding pipeline: research → 3 agents → activate."""
    langfuse_context.update_current_trace(
        name="tenant-onboarding",
        metadata={"tenant_id": str(tenant_id)},
        tags=[str(tenant_id)],
    )
    await set_onboarding_status(session, tenant_id, OnboardingStatus.RUNNING)
    try:
        tenant = await get_tenant(session, tenant_id)

        # Onboarding is fully automated: a hard-to-research company must not fail it.
        # An empty CompanyInfo lets the downstream agents proceed on sparse input.
        company_info = await research(
            goal=_company_goal(str(tenant.website_url), tenant.company_name),
            output_schema=CompanyInfo,
            fallback=CompanyInfo(),
        )

        business_profile: dict[str, Any] = await persona.run(
            company_name=tenant.company_name,
            business_type=tenant.business_type,
            company_info=company_info.model_dump(),
        )
        icp_data = await icp.run(business_profile)
        sigs, weights, thresholds = await signals.run(business_profile, icp_data)

        config_data = TenantConfigCreate(
            business_profile=business_profile,
            icp=icp_data,
            signals=sigs,
            weights=weights,
            thresholds=thresholds,
        )
        await create_active(session, tenant_id, config_data)
        await activate_tenant(session, tenant_id)
        await set_onboarding_status(session, tenant_id, OnboardingStatus.COMPLETE)

    except Exception as exc:
        # Log before re-raising: the FAILED row is the only lasting trace of this run
        # when the caller is a one-off script whose terminal is gone.
        log.exception(
            "onboarding pipeline failed",
            tenant_id=str(tenant_id),
            error=str(exc),
        )
        await set_onboarding_status(session, tenant_id, OnboardingStatus.FAILED)
        raise
