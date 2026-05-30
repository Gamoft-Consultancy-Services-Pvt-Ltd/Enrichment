# shared/tenant — design

**Date:** 2026-05-30
**Status:** Approved (design); implementation pending
**Module:** `shared/tenant` (cross-cutting domain in `shared/`)

## Purpose

`shared/tenant` is the bedrock tenant entity and status lifecycle for the
multi-tenant Lead Intelligence Engine. A *tenant* is the business account /
client workspace that owns all data, rules, and workflows — every piece of data
in the system belongs to exactly one tenant. This module is consulted by many
other modules (scoring, lead_ingestion, orchestration) for tenant identity and
the activation status gate, so it lives in `shared/`, not in a single module.

It is also the project's **first real ORM model**, **first Alembic migration**,
and **first integration tests** — so it sets precedent for every table that
follows.

## Public surface (the boundary)

Per the dependency rule, other code imports only this module's public surface:

- `schemas.py` — Pydantic schemas + the enums (single source of truth).
- `service.py` — async service functions, the only way to mutate/read tenants.

`models.py` (the ORM model) is **internal** — no other module imports it.

## Data model

### Entity — `models.py` (internal), table `tenants`

| Column                 | Type        | Rules                                             |
|------------------------|-------------|---------------------------------------------------|
| `id`                   | UUID        | Primary key, generated `uuid4`                    |
| `company_name`         | str         | required                                          |
| `primary_contact_name` | str         | required                                          |
| `primary_contact_email`| str         | required (validated as email at the schema layer) |
| `business_type`        | enum        | required — `B2B` / `B2C`                           |
| `status`               | enum        | required, default `CREATED`                       |
| `timezone`             | str         | required, default `"UTC"`                          |
| `language_preference`  | str         | required, default `"en"`                           |
| `created_at`           | timestamptz | required, server default `now()`                  |
| `updated_at`           | timestamptz | required, server default `now()`, bumps on update |
| `activated_at`         | timestamptz | nullable — `NULL` until activation                |

**PK naming convention (precedent):** the primary key is always `id`; foreign
keys in other tables are `<entity>_id` (e.g. `lead.tenant_id`). Every future
table follows this.

### Enums — live in `schemas.py` (so `api/`, `models.py`, and tests import them)

- `BusinessType(str, Enum)`: `B2B`, `B2C`. Load-bearing in the domain —
  enrichment branches on it (B2C uses first-party data instead of the
  Surepass→Serper→… cascade), and scoring/ICP logic differs by type. It is a
  defining characteristic of the account, so it lives on the `Tenant` entity
  (not in `tenant_config`).
- `TenantStatus(str, Enum)`: `CREATED`, `ACTIVE`, `SUSPENDED`, `CHURNED`.

The `str` mixin makes each member serialize to its string value (clean JSON and
clean storage as a string).

### Status lifecycle (single merged field)

`status` is the one source of truth for where a tenant is in its lifecycle. The
coarse lifecycle and the onboarding steps were **merged** into this single enum
(no second `current_onboarding_state` field that could drift out of sync). The
fine-grained onboarding progress (the Sonnet agent chain: Business Profile →
Persona → ICP → Signal → prompt) is owned internally by
`modules/tenant_onboarding`, not stored here.

```
CREATED ──activate──▶ ACTIVE ──┬──suspend────▶ SUSPENDED ──reactivate──▶ ACTIVE
                               └──churn──────▶ CHURNED (terminal)
```

- Initial state on creation: `CREATED`.
- "Onboarding" is simply `status == CREATED` (no separate value needed).
- Only the `CREATED → ACTIVE` transition is **implemented now**; `suspend` /
  `churn` transitions are deferred (YAGNI) until a real consumer needs them. The
  enum values exist now because they define the domain vocabulary.

## Schemas — `schemas.py` (public)

- `TenantCreate` — what a caller provides to create a tenant:
  `company_name`, `primary_contact_name`, `primary_contact_email` (`EmailStr`),
  `business_type`, `timezone` (default `"UTC"`), `language_preference`
  (default `"en"`). Deliberately omits `id`, `status`, and timestamps — the
  system assigns those (a client must not be able to POST `status=ACTIVE`).
- `TenantRead` — the full record returned to callers; `from_attributes=True` so
  it is built directly from an ORM `Tenant` object. Contains every column.

`primary_contact_email` uses Pydantic `EmailStr`, which requires adding the
`pydantic[email]` extra (the `email-validator` package) as a dependency.

## Service — `service.py` (public, async, takes `AsyncSession`)

| Function                              | Behaviour                                                                                  |
|---------------------------------------|--------------------------------------------------------------------------------------------|
| `create_tenant(session, data)`        | Insert a tenant with `status=CREATED`; return the new `Tenant`.                            |
| `get_tenant(session, tenant_id)`      | Return the tenant, or raise `NotFoundError` (from `core.exceptions`) if missing.           |
| `activate_tenant(session, tenant_id)` | `CREATED → ACTIVE`, set `activated_at = now()`. Raise `ConflictError` if not `CREATED`.    |
| `is_active(tenant)`                   | Return `status == ACTIVE`. The activation gate other layers consult.                       |

Strictness: `activate_tenant` refuses to activate a tenant that is not currently
`CREATED` (no double activation, no reviving a churned tenant via this path).

## Testing approach

- **Schemas** → fast **unit tests** (no DB): enum membership is exactly the
  expected set; `TenantCreate` accepts valid input and rejects invalid/missing
  `business_type` and invalid emails; `TenantRead` builds from an
  attribute-bearing (ORM-like) object via `from_attributes`.
- **Model + service** → **integration tests** against a real Dockerized Postgres
  (faithful to CI; not aiosqlite). This requires standing up Alembic:
  - Create `migrations/env.py` (and `script.py.mako`) wired to
    `core.db.Base.metadata` and the async `database_url`.
  - Generate the first migration (`tenants` table) via autogenerate; apply with
    `alembic upgrade head`.
  - Add `tests/integration/conftest.py` that runs `alembic upgrade head` against
    the test database and hands each test a rolled-back `AsyncSession` for
    isolation.

## Build order (TDD throughout)

1. `pyproject.toml`: add `pydantic[email]` dependency; `uv sync`.
2. `shared/tenant/schemas.py` + unit tests (no DB) — RED → GREEN.
3. `migrations/env.py` — wire Alembic to `core.db.Base.metadata` + async URL.
4. `shared/tenant/models.py` — the `Tenant` ORM model.
5. First migration — `alembic revision --autogenerate`, then `upgrade head`.
6. `tests/integration/conftest.py` — schema setup + rolled-back session.
7. `shared/tenant/service.py` + integration tests for each function.

## Out of scope (deferred — YAGNI)

- `suspend` / `churn` service transitions and an `is_onboarding` helper.
- Full IANA timezone validation and language-code validation.
- A separate `tenant_config` object (descriptive fields stay on `Tenant` for now).

## Related decisions

- ADR 0001 — held leads drain via a `TenantActivated` event (the activation gate
  here is what onboarding flips; orchestration reacts to the event).
- Boundary rule — no module imports another; shared domain lives in `shared/`.
