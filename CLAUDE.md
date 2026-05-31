# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

**Phase 0 — building the foundation.** Built test-first and merged to `main`:
all `core/` infrastructure (`config.py`, `logging.py`, `lifespan.py` wired into
`main.py`, `exceptions.py`, `db.py`) and the first cross-cutting domain module,
`shared/tenant` (the `Tenant` entity + status lifecycle: public `schemas.py` +
`service.py`, internal `models.py`). Alembic is now wired up (`migrations/env.py`
runs sync via psycopg2 against `core.db.Base.metadata`) with the first migration
creating the `tenants` table, and the integration-test harness
(`tests/integration/conftest.py`) is in place. Still empty stubs: `core/cache.py`,
`core/queue.py` (deferred by YAGNI — build each alongside its first real
consumer), `clients/`, `auth/`, the other `shared/` submodules (`events`,
`tenant_config`, `prompt_registry`, `audit`), and all of `modules/`. Next up is
`shared/events`. When adding the first real code to a module, you are
establishing its public surface — follow the boundary rules below from the start.

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
   signup to `active`. A chain of Sonnet agents (Business Profile → Persona →
   ICP → Signal → prompt generation) builds a locked `PersonaObject`, ICP, signal
   definitions, and a versioned scoring prompt committed to `prompt_registry`
   (`draft → evaluation → active`). Re-runs produce a *new* prompt version and
   deactivate the prior one without disrupting live scoring.
2. **Lead ingestion** (`modules/lead_ingestion`) — accepts leads from four
   sources (Google Sheets pull; Email/WhatsApp/Instagram push), attributes each
   to a tenant, filters genuine leads from noise, normalises them, and emits
   `LeadReceived`. Does **not** enrich or score. WhatsApp/Instagram use a
   conversation-grouping state machine advanced by a per-minute sweeper.
3. **Enrichment** (`modules/enrichment`) — cache-first, cost-escalating external
   lookup: Layer 0 cache → Surepass → Serper → NewsCatcher → Probe42, stopping as
   soon as there's enough to score. B2C uses first-party data instead.
4. **Scoring** (`modules/scoring`) — "Pipeline 1 at runtime". Loads the tenant's
   active prompt + `PersonaObject` + `tenant_config`, scores across five
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
| `make format` | `black .` then `ruff check --fix .` |
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
  `tenant_config`, `prompt_registry`, `audit`, `events`.
- `clients/` — thin wrappers for external services: Surepass, Probe42,
  NewsCatcher, Serper, Sonnet (Anthropic).
- `modules/` — business logic, one folder each: `tenant_onboarding`,
  `lead_ingestion`, `orchestration`, `enrichment`, `scoring`, `reporting`,
  `notification`.
- `api/` — HTTP layer split by audience: `admin`, `tenant`, `public` (public =
  login + health, no auth).
- `workers/` — ARQ background worker entry point and job definitions.

## Stack

FastAPI · uv (package manager) · PostgreSQL via SQLAlchemy 2.0 + Alembic
(async `asyncpg`) · Redis + cachetools (in-process) · ARQ (Redis-backed async
queue) · Anthropic Sonnet for LLM · structlog · pytest.

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
  `mypy core/` run misses). `ruff`/`black` line length is 100 (`E501` ignored).
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
