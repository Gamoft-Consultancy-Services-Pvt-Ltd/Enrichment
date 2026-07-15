# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

**Phase 1 — tenant onboarding pipeline complete.** All of the following have
been built test-first:

**`core/` (complete):** `config.py` (settings incl. `redis_url`, `groq_api_key`),
`logging.py`, `lifespan.py` (inits/closes ARQ pool on startup/shutdown),
`main.py`, `exceptions.py`, `db.py`, `queue.py` (ARQ pool + `get_arq_pool`
FastAPI dep). Still a stub: `core/cache.py` (deferred — build with first cache
consumer).

**`auth/` (complete):** Auth0 JWT verification, `GET /me`, `POST /onboarding`
auth, two roles (`platform_admin`, `tenant`).

**`shared/tenant` (complete):** `Tenant` entity with full status lifecycle
(`CREATED → ACTIVE`); `TenantCreate` requires `website_url: AnyHttpUrl`;
`TenantRead` exposes `website_url: str` and `onboarding_status: OnboardingStatus`
(`PENDING / RUNNING / COMPLETE / FAILED`). `set_onboarding_status` is the
service function the pipeline uses to update progress.

**`shared/tenant_config` (complete):** Versioned per-tenant scoring-config
registry (`tenant_configs` table). Simplified lifecycle: `ACTIVE → ARCHIVED`
only — no DRAFT or human-approval step. `create_active` atomically archives
the previous ACTIVE version and inserts a new one. One partial unique index
enforces a single ACTIVE per tenant. (`prompt_registry` is dropped — this *is*
the registry.)

**`shared/events` (complete, contracts-only):** Frozen `Event` envelope,
`LeadSource`/`LeadBucket` enums, four event types (`TenantActivated`,
`LeadReceived`, `LeadEnriched`, `LeadScored`). No publish/subscribe/bus yet
(delivery lands with its first consumer, `orchestration`, per ADR 0001).

**`clients/groq_client.py` (complete):** Thin async wrapper around an
OpenAI-compatible LLM served by **OpenRouter** (model `deepseek/deepseek-v4-flash`). Despite
the filename, it no longer uses Groq — OpenRouter speaks the OpenAI API protocol,
so the `openai` SDK / `ChatOpenAI` are used as the client, pointed at OpenRouter.
`call_with_tool(prompt, tool_name, tool_description, input_schema)` forces
structured JSON output via function calling (`deepseek/deepseek-v4-flash`, temperature=0);
`get_chat_model()` returns a `ChatOpenAI` for the LangGraph agents.

**`clients/pan_client.py` (complete):** Thin async wrapper around Sandbox
(Quicko) for PAN identity verification. Validates PAN format and checks name +
DOB match against government records.

**`modules/tenant_onboarding` (complete):** Fully automated three-agent pipeline
triggered by `POST /onboarding`. No human review step.
- `agents/persona.py` — website text → structured business profile
- `agents/icp.py` — business profile → ideal customer profile
- `agents/signals.py` — profile + ICP → signals, weights, thresholds
- `kyb.py` — PAN identity verification via Sandbox/Quicko (name + DOB match gate)
- `pipeline.py` — fetches website (httpx), runs agents in sequence, calls
  `create_active`, calls `activate_tenant`, updates `onboarding_status`.
  Sets `RUNNING` on start, `COMPLETE` on success, `FAILED` on any exception.

**`workers/` (complete):** `worker.py` (`WorkerSettings` registers jobs for ARQ).
`workers/jobs/onboarding.py` (`run_onboarding_pipeline` — creates its own DB
session, calls `pipeline.run_pipeline`).

**`api/onboarding.py` (complete):** `POST /onboarding` creates the tenant,
links the user, and immediately enqueues `run_onboarding_pipeline` via the ARQ
pool. The tenant polls `GET /me` for `onboarding_status` to track progress.

**Migrations applied:** `tenants` table (initial), `tenant_configs` table,
`simplify_config_status` (ACTIVE/ARCHIVED only), `add_website_url_onboarding_status_to_tenants`.

**Still empty stubs:** `core/cache.py`, `shared/audit/`, all other `modules/`
(`lead_ingestion`, `orchestration`, `enrichment`, `scoring`, `reporting`,
`notification`), most of `clients/`. **Next up: `modules/lead_ingestion`.**

When adding the first real code to a module, you are establishing its public
surface — follow the boundary rules below from the start.

## What this is

A multi-tenant **Lead Intelligence Engine**: a modular monolith (single
deployable) in Python 3.13 + FastAPI. It ingests leads from multiple sources,
enriches them with external data, scores them against per-tenant Ideal Customer
Profiles (ICPs), buckets them (Hot/Warm/Cold), and serves results through a
dashboard with hot-lead notifications. Two human roles, both via Google OAuth:
`platform_admin` (our team) and `tenant` (the customer).

## Business pipelines (the domain big picture)

Four module-spanning workflows. Each lives behind a module's public service and
communicates across boundaries only via `shared/events` and public services.

1. **Tenant onboarding** (`modules/tenant_onboarding`) — takes a new tenant from
   signup to `active` with no human input. Triggered by `POST /onboarding`
   (tenant provides `website_url`). Onboarding is gated on a synchronous, OTP-free
   PAN identity check via Sandbox/Quicko (`modules/tenant_onboarding/kyb.py` →
   `clients/pan_client.py`): the PAN must be valid and the applicant-supplied name
   + DOB must match. The free-email-domain block and company-email login are
   enforced in Auth0 (see `docs/ops/auth0-kyb-email-gate.md`). An ARQ background
   job fetches the website, then runs three Groq agents in sequence (Persona → ICP
   → Signals) to build a versioned `tenant_config` (business profile, ICP, signal
   definitions, per-dimension weights, thresholds). Writes directly as `ACTIVE` —
   no DRAFT/approval step. Re-runs produce a new version that supersedes the prior
   one without disrupting live scoring. The scoring prompt *template* lives in
   `modules/scoring` code.
2. **Lead ingestion** (`modules/lead_ingestion`) — accepts leads from four
   sources (Google Sheets pull; Email/WhatsApp/Instagram push), attributes each
   to a tenant, filters genuine leads from noise, normalises them, and emits
   `LeadReceived`. Does **not** enrich or score. WhatsApp/Instagram use a
   conversation-grouping state machine advanced by a per-minute sweeper.
3. **Enrichment** (`modules/enrichment`) — cache-first, cost-escalating external
   lookup: Layer 0 cache → Surepass → Serper → NewsCatcher → Probe42, stopping as
   soon as there's enough to score. B2C uses first-party data instead.
4. **Scoring** (`modules/scoring`) — "Pipeline 1 at runtime". Loads the tenant's
   active `tenant_config` version (business profile, ICP, signals, weights,
   thresholds), scores across five
   dimensions (Fit, Intent, Engagement, Behaviour, Context), applies weights and
   thresholds (default HOT ≥ 80, WARM ≥ 55, else COLD), persists score + reasoning
   trace, emits `LeadScored`, and triggers notification for HOT leads (5-min SLA).

**Status gate:** while a tenant is `onboarding`, its leads are HELD (not scored);
on activation they drain in arrival order. The gate is exposed by `shared/tenant`;
the hold/drain mechanism lives in `modules/orchestration`.

## Commands

Use the `Makefile` targets (each wraps a `uv run …` command):

| Command | Purpose |
|---|---|
| `make install` | `uv sync` — create `.venv/`, install deps |
| `make lint` | `ruff check .` |
| `make format` | `ruff format .` then `ruff check --fix .` |
| `make typecheck` | `mypy .` (strict mode) |
| `make test` | full pytest suite |
| `make test-unit` / `make test-integration` / `make test-e2e` | one tier only |
| `make ci` | lint + typecheck + test (the local gate before pushing) |
| `make up` / `make down` / `make logs` | Docker Compose stack |
| `make migrate` | `alembic upgrade head` |

Run a single test: `uv run pytest tests/unit/test_smoke.py::test_name -v`

App without Docker (no DB/Redis): `uv run uvicorn main:app --reload`
Full stack (app + worker + Postgres + Redis): `docker-compose up` → http://localhost:8000/health

Integration tests require real Postgres and Redis (`DATABASE_URL`,
`REDIS_URL`); unit tests do not. `pytest` runs with `asyncio_mode = auto`, so
async test functions need no decorator.

## Architecture — the dependency rule

Dependencies flow strictly inward; **violating this is the main thing to avoid**:

```
main.py → api/ and workers/ → modules/ and auth/ → shared/ → clients/ → core/
```

- **No module imports another module.** If two modules need the same thing, it
  belongs in `shared/`.
- **`core/` imports nothing from the application.**
- Each module exposes a small public surface — typically `service.py` and
  `schemas.py`. Other code imports only that surface, never a module's internals.
  Keeping this discipline is what lets a module later be extracted into its own
  service mechanically rather than via rewrite.

### Layer responsibilities

- `core/` — infrastructure: config, db, cache, queue, logging, lifespan, exceptions.
- `auth/` — Google OAuth, sessions, and two roles: `platform_admin` and `tenant`.
- `shared/` — cross-cutting domain used by many modules: `tenant`,
  `tenant_config`, `audit`, `events`. (`prompt_registry` is dropped — merged
  into `tenant_config`.)
- `clients/` — thin wrappers for external services: `groq_client` (LLM via
  Groq), and stubs for Surepass, Probe42, NewsCatcher, Serper.
- `modules/` — business logic, one folder each: `tenant_onboarding`,
  `lead_ingestion`, `orchestration`, `enrichment`, `scoring`, `reporting`,
  `notification`.
- `api/` — HTTP layer split by audience: `admin`, `tenant`, `public` (public =
  login + health, no auth).
- `workers/` — ARQ background worker entry point and job definitions.

## Stack

FastAPI · uv (package manager) · PostgreSQL via SQLAlchemy 2.0 + Alembic
(async `asyncpg`) · Redis + cachetools (in-process) · ARQ (Redis-backed async
queue) · OpenRouter (`deepseek/deepseek-v4-flash`, OpenAI-compatible, function calling,
temperature=0) · LangGraph + MCP (research agent) · httpx · structlog · pytest.

## Cross-cutting domain rules

- **Calibration (LLM filters):** if a classification is unclear or confidence
  < 0.7, treat the item as a LEAD — a missed lead costs more than a processed
  non-lead.
- **Every cache** needs both an explicit expiry (TTL) *and* an explicit
  invalidation path. PersonaObject cache: in-process `cachetools` TTLCache,
  15-min TTL, key = `(tenant_id, prompt_template_version)`, force-flushed by a
  Postgres `NOTIFY` on prompt-version change (each process listens itself — see
  ADR 0002).
- **External calls** are cache-first and cost-aware: cheapest source first, stop
  as soon as confident, match tools to the tenant's profile (skip layers it
  doesn't need), not to the lead.
- **Scoring is deterministic + explainable:** identical lead data + identical
  config/prompt version must produce an identical score; every score carries
  provenance and a reasoning trace (ADR 0003).

## Key design decisions (see docs/adr/)

- **ADR 0001** — held leads drain via a `TenantActivated` event, not a direct
  call from onboarding into orchestration.
- **ADR 0002** — PersonaObject cache invalidation uses per-process NOTIFY
  listeners (web app and ARQ worker each flush their own cache).
- **ADR 0003** — deterministic scoring via temperature 0 + pinned prompt version
  for now; the score-once-and-cache approach is deferred.
- Recurring pattern: prefer the simpler decoupled option now, defer the heavier
  "Option B" until a real consumer needs it.

## Conventions

- **TDD is the workflow:** write the failing test first, watch it fail for the
  right reason, then minimum code, then refactor. Tests live in `tests/unit`,
  `tests/integration`, `tests/e2e`; shared fixtures in `tests/conftest.py`,
  reusable helpers in `tests/helpers/`.
- `mypy` runs in **strict** mode over the **whole repo** (`mypy .`) — that
  includes `tests/`, so test code must also type-check (this catches issues a
  `mypy core/` run misses). `ruff` line length is 100 (`E501` ignored).
  Pre-commit runs lint/format/typecheck — install once with
  `uv run pre-commit install`.
- **Unit tests must not need a DB or network.** SQLAlchemy connects lazily, so
  building an engine/session touches nothing; structlog output is captured with
  `capsys`. Construct test settings via `tests.helpers.build_settings(...)`,
  which disables `.env` loading for determinism (and centralises the one
  pydantic-settings `_env_file` mypy workaround).
- `pytest` runs with `asyncio_mode = auto`, so async test functions need no
  decorator.
- **Branch protection is by team agreement, not enforced:** never push to
  `main`, open a PR, wait for green CI, don't merge your own PR without a
  teammate's approval.
- Branches: `feature/…`, `fix/…`, `chore/…`. Commits: `feat:`, `fix:`,
  `chore:`, `docs:`, `test:`.
- Record significant design decisions as an ADR in `docs/adr/`.

## Reference

`docs/architecture.md` (folder rationale and the boundary rule), `README.md`
(setup and workflow detail), `docs/adr/` (design decisions).
