# Architecture

## Overview

The Lead Intelligence Engine is a modular monolith written in Python with FastAPI. It is a single deployable that is internally split into independent modules with clean boundaries, so individual modules can later be extracted into separate services without redesign. The structure is organised by responsibility, not by feature.

## Folder structure

```
Enrichment/
├── main.py, pyproject.toml, alembic.ini, Makefile,
│   Dockerfile, docker-compose.yml, README.md, .env.example,
│   .gitignore, .dockerignore, .pre-commit-config.yaml
│
├── core/        — shared infrastructure: config, database, cache, queue, logging
├── auth/        — Google OAuth, sessions, and the two roles (platform_admin, tenant)
├── shared/      — cross-cutting domain concepts used by many modules:
│                  tenant, tenant_config, prompt_registry, audit, events
├── clients/     — wrappers for external services (Surepass, Probe42,
│                  NewsCatcher, Serper, Sonnet)
├── modules/     — business logic, one folder per module:
│                  tenant_onboarding, lead_ingestion, orchestration,
│                  enrichment, scoring, reporting, notification
├── api/         — HTTP layer split by audience: admin, tenant, public
├── workers/     — background worker entry point and job definitions
├── migrations/  — Alembic database migrations
├── scripts/     — one-off operational scripts
├── docs/        — in-repo documentation and ADRs
├── tests/       — unit, integration, end-to-end tests with shared fixtures
└── .github/     — GitHub Actions CI pipeline
```

## Module boundary rule

Each module exposes a small public surface (`service.py` and `schemas.py`) and nothing else. Other modules import only that surface; they never reach into another module's internals. Cross-cutting data lives in `shared/`, never inside a single module. This rule is what keeps the future service extraction mechanical.

## Dependency direction

Dependencies flow strictly inward: `main.py` → `api/` and `workers/` → `modules/` and `auth/` → `shared/` → `clients/` → `core/`. No module imports another module. `core/` imports nothing from the application.

## Project conventions

- **Language:** Python 3.12, FastAPI
- **Package manager:** uv
- **Database:** PostgreSQL with SQLAlchemy and Alembic
- **Cache:** Redis (shared) and cachetools (in-process)
- **Queue:** ARQ (Redis-backed, async)
- **LLM provider:** Anthropic Sonnet
- **Testing:** pytest with test-driven development from day one
- **CI:** GitHub Actions on every push and pull request

## Branch protection (by team agreement)

The GitHub Free tier does not enforce branch protection on private organisation repos. The team enforces it by agreement:

1. Never push directly to `main`. All changes go through a pull request.
2. Wait for CI to be green before merging.
3. No force-pushes to `main`, ever.
4. Do not merge your own pull request without a thumbs-up from another team member.
