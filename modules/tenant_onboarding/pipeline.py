"""Pipeline: fetches the tenant's website and runs the three agents in sequence."""

import re
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from modules.tenant_onboarding.agents import icp, persona, signals
from shared.tenant.schemas import OnboardingStatus
from shared.tenant.service import activate_tenant, get_tenant, set_onboarding_status
from shared.tenant_config.schemas import TenantConfigCreate
from shared.tenant_config.service import create_active


async def run_pipeline(session: AsyncSession, tenant_id: UUID) -> None:
    """Run the full onboarding pipeline: website fetch → 3 agents → activate."""
    await set_onboarding_status(session, tenant_id, OnboardingStatus.RUNNING)
    try:
        tenant = await get_tenant(session, tenant_id)

        async with httpx.AsyncClient(timeout=30.0) as http:
            response = await http.get(str(tenant.website_url))
            response.raise_for_status()
        website_text = _strip_html(response.text)

        business_profile = await persona.run(
            company_name=tenant.company_name,
            business_type=tenant.business_type,
            website_text=website_text,
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

    except Exception:
        await set_onboarding_status(session, tenant_id, OnboardingStatus.FAILED)
        raise


def _strip_html(html: str) -> str:
    """Extract readable text from HTML without external dependencies."""
    text = re.sub(
        r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE
    )
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()
