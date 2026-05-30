# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

**Phase 0 — skeleton only.** The directory tree, CI, Docker setup, and tooling
config are in place, but almost every `core/`, `clients/`, `auth/`, `shared/`,
and `modules/` file is an empty stub. `main.py` exposes only `/health`. When
adding the first real code to a module, you are establishing its public surface —
follow the boundary rules below from the start.

## What this is

A multi-tenant **Lead Intelligence Engine**: a modular monolith (single
deployable) in Python 3.13 + FastAPI. It ingests leads from multiple sources,
enriches them with external data, scores them against per-tenant Ideal Customer
Profiles, and serves results through a dashboard with hot-lead notifications.

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

## Conventions

- **TDD is the workflow:** write the failing test first, then minimum code,
  then refactor. Tests live in `tests/unit`, `tests/integration`, `tests/e2e`;
  shared fixtures in `tests/conftest.py`.
- `mypy` runs in **strict** mode; `ruff`/`black` line length is 100 (`E501`
  ignored). Pre-commit runs lint/format/typecheck — install once with
  `uv run pre-commit install`.
- **Branch protection is by team agreement, not enforced:** never push to
  `main`, open a PR, wait for green CI, don't merge your own PR without a
  teammate's approval.
- Branches: `feature/…`, `fix/…`, `chore/…`. Commits: `feat:`, `fix:`,
  `chore:`, `docs:`, `test:`.
- Record significant design decisions as an ADR in `docs/adr/`.

## Reference

`docs/architecture.md` (folder rationale and the boundary rule), `README.md`
(setup and workflow detail).
