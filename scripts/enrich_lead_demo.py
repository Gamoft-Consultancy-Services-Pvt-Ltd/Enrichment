"""Manual end-to-end demo: ingest a lead, enrich it, store it on the lead row.

Drives the DB directly (no Auth0) the way integration tests do:
  1. insert an ACTIVE tenant + a lead (fake contact details, real company in extra_fields),
  2. build a LeadPayload from the lead and run the real enrichment (Serper + Groq via MCP),
  3. persist the EnrichmentResult onto leads.enrichment / leads.enriched_at,
  4. re-read the row from Postgres and print it.

Run inside the app container (needs DATABASE_URL, GROQ/SERPER keys, and the MCP server):
    docker compose up -d                       # postgres, redis, mcp-web-search
    docker compose run --rm -e PYTHONPATH=/app -e PYTHONUNBUFFERED=1 \
        app /app/.venv/bin/python scripts/enrich_lead_demo.py
"""

import asyncio
import json
import os

from core.db import async_session_factory
from core.exceptions import ExternalServiceError
from modules.enrichment.service import run_enrichment
from modules.lead_ingestion.service import Lead, get_lead_by_id, store_lead_enrichment
from shared.events.schemas import EnrichmentResult, LeadPayload, LeadSource
from shared.tenant.models import Tenant
from shared.tenant.schemas import BusinessType, OnboardingStatus, TenantStatus


async def main() -> None:
    async with async_session_factory() as session:
        tenant = Tenant(
            company_name="AcmeCRM (enrich demo)",
            primary_contact_name="Demo Admin",
            primary_contact_email="admin@acmecrm.example",
            business_type=BusinessType.B2B,
            website_url="https://acmecrm.example",
            onboarding_status=OnboardingStatus.COMPLETE,
            status=TenantStatus.ACTIVE,
            pan="ABCDE1234F",
        )
        session.add(tenant)
        await session.flush()

        lead = Lead(
            tenant_id=tenant.id,
            pipeline_stage="received",
            source_channel="EMAIL",
            full_name="Jane Doe",
            phone="+15550001234",
            email="jane.doe.demo@fake-nonexistent.invalid",
            location="San Francisco, CA",
            raw_event_json={"demo": True},
            extra_fields={
                "company": "Stripe",
                "job_title": "VP Operations",
                "inbound_message": "Evaluating payment infrastructure for our marketplace.",
            },
        )
        session.add(lead)
        await session.commit()
        lead_id = lead.id
        print(f"[demo] created tenant {tenant.id} + lead {lead_id} (company=Stripe)", flush=True)

        extra = lead.extra_fields or {}
        payload = LeadPayload(
            name=lead.full_name,
            email=lead.email,
            phone=lead.phone,
            company=extra.get("company"),
            source=LeadSource.EMAIL,
            first_party=extra,
        )

        fallback = EnrichmentResult(
            company_info={"note": "DETERMINISTIC FALLBACK — live LLM enrichment skipped/unavailable"},
            confidence=0.0,
            reasoning_trace="Fallback: live enrichment not run; stored to demonstrate the DB path.",
        )
        if os.getenv("DEMO_SKIP_LIVE"):
            print("[demo] DEMO_SKIP_LIVE set — storing deterministic result (no LLM call).", flush=True)
            result = fallback
        else:
            print("[demo] running enrichment (real Serper + Groq, may take ~1-2 min)...", flush=True)
            try:
                result = await run_enrichment(payload, tenant)  # type: ignore[arg-type]  # Tenant has .business_type
                print("[demo] LIVE enrichment succeeded", flush=True)
            except ExternalServiceError as exc:
                print(f"[demo] LIVE enrichment FAILED ({exc}).", flush=True)
                print("[demo] storing the deterministic fallback to demonstrate persistence.", flush=True)
                result = fallback
        await store_lead_enrichment(session, lead_id, result)
        await session.commit()
        print("[demo] enrichment stored on the lead row", flush=True)

        session.expunge_all()
        fresh = await get_lead_by_id(session, lead_id)
        assert fresh is not None
        print("\n=== LEAD ROW (read back from Postgres) ===", flush=True)
        print(f"id           : {fresh.id}", flush=True)
        print(f"tenant_id    : {fresh.tenant_id}", flush=True)
        print(f"email        : {fresh.email}  (fake)", flush=True)
        print(f"company      : {(fresh.extra_fields or {}).get('company')}  (real)", flush=True)
        print(f"enriched_at  : {fresh.enriched_at}", flush=True)
        print(f"enrichment   :\n{json.dumps(fresh.enrichment, indent=2)}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
