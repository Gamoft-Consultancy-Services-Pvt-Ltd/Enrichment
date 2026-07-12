"""Orchestration: drive a received lead through the enrichment step.

process_lead is the pipeline mediator — it sequences public services across
modules (tenant lookup → enrichment → persistence). It is the seam where the
hold/drain gate (before enrichment) and scoring (after) will slot in later.
Under the lite coupling rule it may import other modules' public service.py.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from modules.enrichment.service import run_enrichment
from modules.lead_ingestion.service import store_lead_enrichment
from shared.events.schemas import LeadReceived
from shared.tenant.schemas import TenantRead
from shared.tenant.service import get_tenant


async def process_lead(session: AsyncSession, event: LeadReceived) -> None:
    """Enrich the lead carried on `event` and persist the result on its row.

    Assumes the tenant is ACTIVE (the hold/drain gate is not built yet).
    Idempotent: store_lead_enrichment overwrites, so a redelivered event is safe.
    """
    tenant = await get_tenant(session, event.tenant_id)
    result = await run_enrichment(event.payload, TenantRead.model_validate(tenant))
    await store_lead_enrichment(session, event.lead_id, result)
