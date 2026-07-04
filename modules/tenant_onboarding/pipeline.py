"""Pipeline: fetches the tenant's website and runs the three agents in sequence."""

import asyncio
import re
from urllib.parse import urlparse
from uuid import UUID

import httpx
from langfuse.decorators import langfuse_context, observe
from sqlalchemy.ext.asyncio import AsyncSession

from clients.serper_client import search_site_pages
from modules.tenant_onboarding.agents import icp, persona, signals
from shared.tenant.schemas import OnboardingStatus
from shared.tenant.service import activate_tenant, get_tenant, set_onboarding_status
from shared.tenant_config.schemas import TenantConfigCreate
from shared.tenant_config.service import create_active


@observe(capture_input=False)
async def run_pipeline(session: AsyncSession, tenant_id: UUID) -> None:
    """Run the full onboarding pipeline: website fetch → 3 agents → activate."""
    langfuse_context.update_current_trace(
        name="tenant-onboarding",
        metadata={"tenant_id": str(tenant_id)},
        tags=[str(tenant_id)],
    )
    await set_onboarding_status(session, tenant_id, OnboardingStatus.RUNNING)
    try:
        tenant = await get_tenant(session, tenant_id)

        async with httpx.AsyncClient(timeout=30.0) as http:
            domain = _extract_domain(str(tenant.website_url))
            urls = await search_site_pages(domain, num=5)
            if urls:
                gather_results = await asyncio.gather(
                    *[http.get(u) for u in urls], return_exceptions=True
                )
                page_results: list[httpx.Response | BaseException] = list(gather_results)
                website_text = _combine_page_texts(page_results)
            else:
                website_text = ""
            if not website_text:
                website_text = await _fallback_fetch(http, str(tenant.website_url))

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


def _extract_domain(url: str) -> str:
    """Return the bare hostname from a URL ('https://acme.com/x' → 'acme.com')."""
    return urlparse(url).netloc


def _combine_page_texts(results: list[httpx.Response | BaseException]) -> str:
    """Strip HTML from successful responses and join with double newlines."""
    parts = []
    for r in results:
        if isinstance(r, BaseException):
            continue
        if r.status_code != 200:
            continue
        parts.append(_strip_html(r.text))
    return "\n\n".join(parts)


async def _fallback_fetch(http: httpx.AsyncClient, url: str) -> str:
    """Fetch url directly and return stripped plain text."""
    response = await http.get(url)
    response.raise_for_status()
    return _strip_html(response.text)


def _strip_html(html: str) -> str:
    """Extract readable text from HTML without external dependencies."""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()
