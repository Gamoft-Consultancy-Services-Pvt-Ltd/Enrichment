# Orchestration: automatic ingestion → enrichment trigger

- **Status:** Design (approved in brainstorming, pending spec review)
- **Date:** 2026-07-12
- **Branch:** `feature/enrichment-module`

## Problem

`lead_ingestion` already builds a `LeadReceived` event on the happy path
(`modules/lead_ingestion/pipeline.py`), but the ARQ worker jobs that run capture
(`workers/jobs/lead_ingestion.py`) **discard the returned event**
(`await run_capture(...)` with the result ignored). Consequently nothing triggers
enrichment: `enrichment.service.run_enrichment` is only ever called by
`scripts/enrich_lead_demo.py` and tests.

Goal: make ingestion **automatically** trigger enrichment as a decoupled
background step, so the ingestion → enrichment hop works end-to-end and can be
exercised manually. This is the pipeline's first real cross-module runtime hop.

## Decisions and rationale

1. **Delivery = direct ARQ enqueue, not an event bus.** Asynchronous decoupling
   (ingestion must not block on the 1–2 min enrichment LLM call) is provided by
   the ARQ job queue itself, not by a bus. A pub/sub bus's distinctive value is
   *fan-out* to multiple/unknown consumers of one event; the project only has
   that at `LeadScored` (notification + reporting) and `TenantActivated` (ADR
   0001), whose consumers are still empty stubs. Per the project's recurring
   "prefer the simpler decoupled option now, defer Option B until a real consumer
   needs it" pattern, the bus is deferred until a second consumer of the same
   event actually exists.

2. **Coupling = lite rule.** Modules may import another module's **public
   `service.py`** surface, never its internals; no circular dependencies. This
   relaxes the strict "no module imports another module" rule. The strict rule's
   stated payoff — mechanical extraction of a module into its own service — is
   not relevant at current scale (0 customers, ~2 years to 5), so the everyday
   indirection cost is not justified. The cheap, high-value part of the
   discipline (public surfaces only, no internals, no cycles) is kept.

3. **Scope = enrichment trigger only.** The hold/drain gate, `LeadEnriched`
   emission, scoring, notification, and Shopify order-history deps
   (`EnrichmentDeps`) are all out of scope (see below).

4. **`process_lead` assumes the tenant is ACTIVE — no status guard now.** A
   status check with nowhere to *hold* the lead is a half-built gate: it would
   either drop onboarding tenants' leads or need the deferred hold/drain
   mechanism. Ingestion's `check_pre_flight` already halts capture when a tenant
   has no active config, so a `LeadReceived` generally implies a ready tenant.
   Held-lead behaviour is the deferred gate's responsibility.

5. **Idempotency.** ARQ delivery is at-least-once, so the handler must be safe to
   run twice. It is: `store_lead_enrichment` overwrites `leads.enrichment`. To
   also avoid duplicate LLM spend on redelivery, the enrichment job is enqueued
   with an explicit job id `enrich:{lead_id}` (idempotency key) so a re-enqueue
   for the same lead collapses into one job while it is still known to ARQ.

## Architecture

```
webhook / file-upload
  └─ existing ingestion ARQ job (run_lead_capture / _batch / _ad_capture)
       └─ run_capture() → (Lead, LeadReceived | None)   [lead already committed]
            └─ if LeadReceived:  enqueue_job("run_lead_pipeline", event_json,
                                             _job_id=f"enrich:{lead_id}")
                 └─ NEW run_lead_pipeline ARQ job
                      └─ orchestration.service.process_lead(session, event)
                           ├─ get_tenant()            (shared/tenant, public)
                           ├─ run_enrichment()        (modules/enrichment, public)
                           └─ store_lead_enrichment() (modules/lead_ingestion, public)
```

### Components

**`modules/orchestration/service.py` (new) — the mediator.**
```python
async def process_lead(session: AsyncSession, event: LeadReceived) -> None:
    tenant = await get_tenant(session, event.tenant_id)
    result = await run_enrichment(event.payload, TenantRead.model_validate(tenant))
    await store_lead_enrichment(session, event.lead_id, result)
```
Imports only the public services of `shared.tenant`, `modules.enrichment`, and
`modules.lead_ingestion` (lite coupling). The ORM `Tenant` is converted to
`TenantRead` so enrichment's public contract stays honest (no `type: ignore`).
This function is the seam where the gate (before enrichment) and scoring (after)
will slot in later.

**`workers/jobs/orchestration.py` (new) — the job wrapper.** Rebuilds
`LeadReceived` from the dict payload, opens a session from
`ctx["session_factory"]`, calls `process_lead`, commits. Registered in
`WorkerSettings.functions` in `workers/worker.py`.

**`workers/jobs/lead_ingestion.py` (edit) — the publish seam.** The three sites
that currently discard the capture result capture the tuple and enqueue via a
small helper:
```python
async def _enqueue_pipeline(ctx, received: LeadReceived | None) -> None:
    if received is not None:
        await cast(ArqRedis, ctx["redis"]).enqueue_job(
            "run_lead_pipeline",
            received.model_dump(mode="json"),
            _job_id=f"enrich:{received.lead_id}",
        )
```
Enqueue happens **after** ingestion has committed the lead, so the row exists
when the pipeline job runs (no read-after-write race).

## Error handling

- `WorkerSettings.max_tries = 2` (already set) → a failed enrichment retries once,
  then dead-letters (logged). A failure leaves the lead un-enriched
  (`enrichment` NULL) but intact — ingestion committed it in its own transaction
  before enqueuing, so there is no data loss.
- At-least-once ⇒ `process_lead` is idempotent (overwrite) and de-duped by the
  `enrich:{lead_id}` job id.

## Testing (TDD, test-first)

- **Unit — `process_lead`:** mock `get_tenant`, `run_enrichment`,
  `store_lead_enrichment`; assert call order and argument wiring
  (`event.payload`, converted `TenantRead`, `event.lead_id`). No DB/network.
- **Unit — publish seam:** assert `run_lead_pipeline` is enqueued with the right
  payload and `_job_id` when `run_capture` yields an event, and **not** enqueued
  when it returns `None` (mock `ctx["redis"]`).
- **Integration — full hop:** real Postgres, `shared.research.agent.research`
  stubbed (no network); drive a capture → run the pipeline job → assert
  `leads.enrichment` is populated and `leads.enriched_at` set.
- **Manual E2E:** existing `scripts/enrich_lead_demo.py`.

## Out of scope (deferred)

- Hold/drain gate (tenant-status hold + `TenantActivated` drain in arrival order).
- `LeadEnriched` emission (no consumer until scoring exists).
- `modules/scoring`, `modules/notification`.
- The event bus.
- Shopify order-history deps (`EnrichmentDeps`); `run_enrichment` is called with
  `deps=None`.

## Prerequisite (orthogonal)

Live enrichment requires `OPENROUTER_API_KEY` to be set (currently empty in
`.env`); the production-secrets guard in `core/config.py` also still requires the
dead `groq_api_key` instead of `openrouter_api_key`. Tracked separately from this
design.
