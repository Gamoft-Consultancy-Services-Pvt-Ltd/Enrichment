# Langfuse AI Observability — Design Spec

**Status:** Approved
**Date:** 2026-06-11 (decisions confirmed 2026-06-23)
**Implementation plan:** `docs/superpowers/plans/2026-06-11-langfuse-observability.md`

## Goal

Give the team visibility into what the onboarding agents actually do at runtime —
the prompt sent, the structured output returned, token usage, and latency for
every LLM call — and group those calls into one trace per onboarding run, tagged
by tenant. This makes debugging and "understanding the agents" possible without
adding logging plumbing to each module.

## Non-goals

- No production-scale observability platform. Expected load is ~5 tenants over
  6 months, i.e. dozens-to-hundreds of traces total. Capacity is a non-issue.
- No per-module instrumentation API. Instrumentation lives at two shared choke
  points only; future code that calls Groq is traced automatically.
- No alerting, dashboards, or evals in this iteration — just trace capture.

## Key decisions

1. **Langfuse v2, self-hosted (single container).** At our scale, v2's
   single-container + existing-Postgres footprint is the right operational
   trade-off. v3's OpenTelemetry architecture (ClickHouse + Redis + blob store)
   buys throughput and ecosystem features we will not use for a long time, if
   ever. v2 still receives security/bug fixes. Migration to v3 later is a
   contained change (the `@observe` decorators carry over; the `langfuse_context`
   calls would be swapped).
2. **Instrument at two choke points, not per-module:**
   - `clients/groq_client.call_with_tool` — `@observe(as_type="generation")`. Every
     LLM call in any module becomes a traced generation recording model, prompt,
     structured output, and token usage.
   - `modules/tenant_onboarding/pipeline.run_pipeline` — `@observe()`. One trace
     per onboarding run, tagged with `tenant_id`. The agent generations nest
     underneath automatically.
3. **Disabled by default, silent no-op.** With no Langfuse keys configured, the
   SDK is explicitly disabled so every `@observe()` decorator is a no-op. Unit
   tests and local runs without Langfuse behave exactly as before — no network,
   no behavioural change.
4. **Self-hosted data stays in our Postgres.** The Langfuse container uses a
   separate `langfuse` database inside the existing Postgres container. Trace
   data stays in the deployment and account signup is local, not Langfuse Cloud.
   (Self-hosted Langfuse v2 does send anonymous *usage* telemetry to PostHog by
   default — never trace/prompt content — which we disable via
   `TELEMETRY_ENABLED=false` in the compose stack.)

## Forward compatibility note (agentic enrichment)

A planned redesign replaces the onboarding pipeline's direct website fetch with
an **agentic web-search research agent** (a multi-turn ReAct loop over Serper),
later reused for lead enrichment. That work will add a new *multi-turn*
tool-calling method to `clients/groq_client.py` (today's `call_with_tool` is
single-shot). When it lands, that new method must also carry the
`@observe(as_type="generation")` decoration so every loop iteration is traced —
which is exactly why observability is being built first. This spec does not
implement that method; it only notes the choke-point convention it must follow.

## Architecture

```
                         ┌─────────────────────────────┐
  POST /onboarding ──▶ ARQ worker ── run_pipeline @observe()  (trace: tenant-onboarding,
                         │                               │       tagged tenant_id)
                         │   persona / icp / signals     │
                         │     └─ call_with_tool @observe(generation)  ×N
                         └──────────────┬───────────────-┘
                                        │ batched async export
                                        ▼
                          langfuse/langfuse:2 container ──▶ Postgres (db: langfuse)
                                        ▲
                                   UI :3000
```

### Components

| Component | Responsibility | Depends on |
|---|---|---|
| `core/observability.py` | Configure the SDK from settings (`configure_langfuse`); flush buffered traces (`flush_langfuse`). Disabled when keys are empty. | `langfuse.decorators`, `core.config` |
| `core/config.py` (settings) | `langfuse_public_key`, `langfuse_secret_key`, `langfuse_host` (empty keys ⇒ disabled). | pydantic-settings |
| `clients/groq_client.py` | `@observe(as_type="generation")` + record model/input/output/usage after each call. | langfuse SDK |
| `modules/tenant_onboarding/pipeline.py` | `@observe()` on `run_pipeline`; tag trace with `tenant_id`. | langfuse SDK |
| `workers/worker.py` | `on_startup` configures the SDK; `on_shutdown` flushes buffered traces so nothing is lost when the worker exits. | `core.observability` |
| `docker-compose.yml` | `langfuse` service (port 3000, `DATABASE_URL` → `langfuse` DB). | existing Postgres |

## Data flow

1. `POST /onboarding` enqueues the ARQ job; the worker `on_startup` hook has
   already called `configure_langfuse()`.
2. `run_pipeline` begins → `@observe()` opens a trace; first statement tags it
   with `tenant_id` (metadata + tag).
3. Each agent calls `call_with_tool` → `@observe(as_type="generation")` opens a
   child generation; after the Groq response, `update_current_observation`
   records model, prompt, parsed output, and token usage.
4. The SDK batches and exports asynchronously to the Langfuse container.
5. On worker shutdown, `flush_langfuse()` delivers any buffered traces.

## Error handling

- **No keys ⇒ disabled.** `configure_langfuse` calls
  `langfuse_context.configure(enabled=False)`; all decorators no-op. No network,
  no failures.
- **Observability never breaks the pipeline.** Tracing is side-channel; the
  `@observe` decorators do not alter return values or raise into business logic.
  A failed onboarding run still produces a trace marked errored at the failing
  step.
- **Non-serializable inputs.** `run_pipeline` takes a SQLAlchemy session, so it
  is decorated `@observe(capture_input=False)`; the useful context (`tenant_id`)
  is set explicitly instead.

## Testing strategy

All unit tests run with the SDK disabled (no keys), so the decorators no-op and
existing tests are unaffected. New unit tests (no network) cover:

- **Settings:** Langfuse defaults empty / host default; overridable.
- **Bootstrap:** `configure_langfuse` disables when keys missing, enables with
  the right kwargs when present; `flush_langfuse` delegates to the SDK.
- **Groq client:** the generation records name, model, input, output, and
  `{input, output}` token usage (SDK context patched).
- **Pipeline:** the trace is tagged `name="tenant-onboarding"`, metadata +
  tags carry `tenant_id` (SDK context patched).
- **Worker:** `WorkerSettings` registers the startup/shutdown hooks; startup
  configures, shutdown flushes.

Manual end-to-end verification: bring up the `langfuse` container, create a
local project, set keys in `.env`, run a real onboarding, and confirm one
`tenant-onboarding` trace with the nested agent generations (prompt, output,
tokens, latency) appears in the UI.
