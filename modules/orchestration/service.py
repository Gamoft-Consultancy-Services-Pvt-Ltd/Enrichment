"""Orchestration: drive a received lead through enrichment, then scoring.

process_lead is the pipeline mediator — it sequences public services across
modules (tenant lookup → enrichment → persistence → scoring → persistence).
The hold/drain gate still slots in before enrichment. Under the lite coupling
rule it may import other modules' public service.py.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from modules.enrichment.service import run_enrichment
from modules.lead_ingestion.service import store_lead_enrichment, store_lead_score
from modules.scoring.service import run_scoring
from shared.events.schemas import LeadReceived
from shared.tenant.schemas import TenantRead
from shared.tenant.service import get_tenant
from shared.tenant_config.service import get_active_config

logger = logging.getLogger(__name__)


async def process_lead(session: AsyncSession, event: LeadReceived) -> None:
    """Enrich the lead carried on `event`, score it, and persist both results.

    Assumes the tenant is ACTIVE (the hold/drain gate is not built yet).
    Idempotent: both stores overwrite, so a redelivered event is safe.
    """
    tenant = await get_tenant(session, event.tenant_id)
    result = await run_enrichment(event.payload, TenantRead.model_validate(tenant))
    lead = await store_lead_enrichment(session, event.lead_id, result)

    config = await get_active_config(session, event.tenant_id)
    if config is None:
        # Not retryable — a retry cannot create a config, and raising here would
        # discard the enrichment we just persisted. Loud, but not fatal.
        logger.error(
            "scoring skipped: tenant %s has no ACTIVE tenant_config (lead %s)",
            event.tenant_id,
            event.lead_id,
        )
        return

    scoring = await run_scoring(config, result, lead)
    await store_lead_score(session, event.lead_id, scoring)
    logger.info(
        "lead %s scored %s (%s), coverage %s",
        event.lead_id,
        scoring.total_score,
        scoring.bucket,
        scoring.coverage,
    )
