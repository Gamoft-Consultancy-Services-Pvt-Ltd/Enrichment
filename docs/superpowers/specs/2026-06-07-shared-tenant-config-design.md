# shared/tenant_config — design

**Date:** 2026-06-07
**Status:** Approved (design); implementation pending
**Module:** `shared/tenant_config` (cross-cutting domain in `shared/`)

## Purpose

`shared/tenant_config` is a **versioned registry of per-tenant scoring
configuration**. The `tenant_onboarding` agent chain produces a config version
per tenant — an enriched business profile, the derived Ideal Customer Profile
(ICP), the signal definitions, the per-dimension weights, and the score
thresholds — and a human approves it before it goes live. `modules/scoring`
reads the single **active** version to score each lead.

It is a *registry*, not a single config row: a tenant has **many versions over
time** (one active, the rest archived or rejected), so re-running onboarding
produces a new version and supersedes the old one **without disrupting live
scoring**, and a prior version can be rolled back to.

### Relationship to `prompt_registry` (which is dropped)

The original architecture split this concern into two `shared/` modules:
`prompt_registry` (versioned prompt text, `draft → evaluation → active`) and
`tenant_config` (weights/thresholds/signals). This design **merges them into
`tenant_config` and drops `prompt_registry`**: the scoring *prompt template*
lives in `modules/scoring` code (versioned via git), and only the *variable*
parts a tenant supplies — business profile, ICP, signals, weights, thresholds —
live here. The versioning/lifecycle machinery that `prompt_registry` would have
owned moves onto `tenant_config`, which is what makes it a registry.

`shared/prompt_registry`'s empty stub should be removed as part of this work.

## Why a separate, versioned table (not columns on `tenants`)

The decisive reason is **versioning**. During a re-run of an already-live
tenant, a `DRAFT` (pending human approval) and the current `ACTIVE` version
**coexist** for the same tenant — two config versions at once, which a single
`tenants` row cannot represent. Versioning also gives rollback (revert to an
archived version) and audit (rejected drafts are retained).

Supporting reasons:

- **Timing/nullability:** a tenant exists in `CREATED` status *before* any
  config exists (the agent chain runs later). "No config row yet" cleanly models
  "not configured yet," rather than a row of nullable columns on `tenants`.
- **Row weight & access pattern:** `tenants` is read constantly for the
  `is_active` gate; it should stay a lean identity/status row, not carry large
  JSON scoring payloads. (Large JSON would be TOASTed out-of-line anyway, so the
  performance difference is negligible — this is a *clarity/ownership* argument,
  not a speed one.)
- **Boundary/ownership:** `tenants` is owned internally by `shared/tenant`;
  scoring config is a distinct concern read by `scoring`. Keeping them separate
  keeps each module's surface focused.

A denormalized "active config copy on `tenants`" was considered and rejected: it
saves at most one index lookup on a read that `scoring` will serve from an
in-process cache anyway, at the cost of dual-writes on every activation and a
copies-can-drift risk.

## Ownership and boundaries

- **Structural owner:** `shared/tenant_config` owns the table and the public
  service that enforces the invariants (version numbering, the atomic
  activation flip, "at most one active/draft per tenant").
- **Driven by:** `modules/tenant_onboarding` — runs the agent chain, then calls
  this module's service to persist a draft and (on human approval) activate it.
  *Mechanism lives in `shared`; policy (what to build, when to approve) lives in
  the module.*
- **Read by:** `modules/scoring` — calls `get_active_config(tenant_id)`.
- **Placement:** lives in `shared/` because two modules need it and **no module
  may import another module**. On a future service extraction the table moves
  into the onboarding service and `scoring` becomes an event+cache consumer; the
  public service surface defined here is precisely that future API seam.

## Data model

A new ORM model `TenantConfig` on `core.db.Base`, table `tenant_configs`,
registered in `migrations/env.py` so Alembic autogenerate sees it. A new
migration creates the table and the two partial unique indexes.

```
tenant_configs
├─ id                UUID         PK (default uuid4)
├─ tenant_id         UUID         FK → tenants.id, not null, indexed
├─ version           int          per-tenant, monotonic (1, 2, 3, …), not null
├─ status            enum         DRAFT | ACTIVE | ARCHIVED | REJECTED, not null
├─ business_profile  JSONB        enriched profile of the tenant's own business
├─ icp               JSONB        ideal-customer narrative, derived from business_profile
├─ signals           JSONB        list of {id, dimension, question}
├─ weights           JSONB        {fit, intent, engagement, behaviour, context}
├─ thresholds        JSONB        {hot, warm}
├─ created_at        timestamptz  server_default now(), not null
├─ activated_at      timestamptz  null until the version becomes ACTIVE
└─ archived_at       timestamptz  null until the version becomes ARCHIVED or REJECTED
```

Notes:

- All five payload fields use Postgres **`JSONB`** (binary, queryable). The DB
  does not enforce their internal shape — pydantic does (see Validation).
- `status` is a SQLAlchemy `Enum` over a `StrEnum` `ConfigStatus`, matching the
  `TenantStatus`/`BusinessType` precedent in `shared/tenant/schemas.py`.
- `(tenant_id, version)` is unique — a tenant never reuses a version number.
- `business_profile` and `icp` are intentionally **unconstrained** JSON objects:
  their internal shape is the agent chain's output, decided when
  `tenant_onboarding` is built — this spec does not lock it. Only `signals`,
  `weights`, and `thresholds` are structured and validated, because `scoring`
  depends on their shape.

### Integrity — partial unique indexes

```sql
CREATE UNIQUE INDEX uq_active_config_per_tenant
  ON tenant_configs (tenant_id) WHERE status = 'ACTIVE';

CREATE UNIQUE INDEX uq_draft_config_per_tenant
  ON tenant_configs (tenant_id) WHERE status = 'DRAFT';
```

At most one `ACTIVE` and at most one `DRAFT` per tenant; `ARCHIVED` and
`REJECTED` rows are unbounded.

## Lifecycle

```
  generate → DRAFT ──approve──→ ACTIVE ──superseded──→ ARCHIVED
                 └───reject────→ REJECTED
```

- **Create draft:** the agent chain finishes (in application memory — its
  multi-step Sonnet calls do not hold a DB transaction open), then a `DRAFT` row
  is written. The draft persists across the human-approval gap, which may span
  many requests over hours or days — this is *why* `DRAFT` must be a durable DB
  state rather than in-memory.
- **Approve (`DRAFT → ACTIVE`):** in **one transaction**, set the draft to
  `ACTIVE` (set `activated_at`) and set any current `ACTIVE` version to
  `ARCHIVED` (set `archived_at`). On a tenant's **first-ever** activation this is
  also the trigger to flip the tenant `CREATED → ACTIVE` and emit
  `TenantActivated` (ADR 0001) — that cross-module step is driven by
  `tenant_onboarding`, which calls `shared/tenant`'s `activate_tenant`; it is not
  this module's job.
- **Reject (`DRAFT → REJECTED`):** set `archived_at`. Retained for audit and as
  an agent-quality signal. Never a rollback target. Onboarding then re-runs to
  produce a fresh draft.
- **Rollback:** re-activate an `ARCHIVED` version (archiving the current
  `ACTIVE`). Only `ARCHIVED` versions are eligible — never `REJECTED`.

`DRAFT`-time evaluation (an `EVALUATION` status that tests a draft against
historical leads before activation) is **deferred** — add it when there is
something to evaluate against.

## Scoring contract (consumed by `modules/scoring`)

Scoring is **binary and two-level**:

```
dimension_score[d] = yes_count[d] ÷ question_count[d]        # binary answers, equal weight
total              = Σ  weights[d] × dimension_score[d]       # d ∈ the five dimensions
                     d
bucket: total×100 ≥ hot → HOT ;  ≥ warm → WARM ;  else COLD
```

- **Signals are questions.** Each signal is a yes/no question evaluated against
  the enriched lead. **Within a dimension, questions are equal-weight**, so a
  dimension's score is the fraction answered "yes." An ideal customer answers
  "yes" to everything and scores `1.0` across all dimensions.
- **Across dimensions, weights are per-tenant**, set by the agent chain based on
  the tenant's business type, and sum to `1.0`. (This is the per-tenant tuning;
  signals carry no per-question weight.)
- **Binary, not graded.** Each question is yes (1) or no (0). We deliberately do
  **not** use LLM-reported confidence as a per-question value — self-reported
  confidence is poorly calibrated; a committed yes/no is more defensible and far
  easier to debug.
- **Every dimension has at least one question** (enforced by validation), so the
  `÷ question_count` denominator is never zero. A dimension that "barely matters"
  for a tenant is expressed by a **low weight**, not by having zero questions.

## Public surface (the boundary)

Two files, matching `shared/tenant`'s structure (`schemas.py` + `service.py`,
plus this module's `models.py`). Consumers import only `schemas` and `service`,
never `models`.

`schemas.py` — the public types:

- `ConfigStatus(StrEnum)` — `DRAFT | ACTIVE | ARCHIVED | REJECTED`.
- `Dimension(StrEnum)` — `FIT | INTENT | ENGAGEMENT | BEHAVIOUR | CONTEXT`.
- `Signal(BaseModel)` — `id: str`, `dimension: Dimension`, `question: str`.
- `Weights(BaseModel)` — `fit, intent, engagement, behaviour, context: float`
  (validates each ∈ [0,1] and the sum ≈ 1.0 within tolerance 1e-6).
- `Thresholds(BaseModel)` — `hot: int`, `warm: int` (validates both ∈ [0,100]
  and `hot > warm`).
- `TenantConfigCreate(BaseModel)` — the draft payload: `business_profile: dict`,
  `icp: dict`, `signals: list[Signal]`, `weights: Weights`,
  `thresholds: Thresholds`. Cross-field validation: signals non-empty, each
  `id` unique within the version, and **all five dimensions represented at least
  once**.
- `TenantConfigRead(BaseModel, from_attributes=True)` — the full record returned
  to callers (id, tenant_id, version, status, the five payloads, timestamps).

`service.py` — the only path to read/write configs:

- `get_active_config(session, tenant_id) -> TenantConfigRead | None` — the one
  `ACTIVE` version, or `None` if the tenant has none yet.
- `create_draft(session, tenant_id, data: TenantConfigCreate) -> TenantConfigRead`
  — assigns the next `version` for the tenant, writes a `DRAFT`. Raises
  `ConflictError` if a `DRAFT` already exists for the tenant (one-draft rule).
- `approve_version(session, config_id) -> TenantConfigRead` — atomic flip:
  target `DRAFT`/`ARCHIVED` → `ACTIVE`, current `ACTIVE` → `ARCHIVED`. Raises
  `ConflictError` if the target is `REJECTED` (not eligible) or already
  `ACTIVE`; `NotFoundError` if it does not exist. Accepting an `ARCHIVED` target
  is the rollback path.
- `reject_version(session, config_id) -> TenantConfigRead` — `DRAFT → REJECTED`.
  `ConflictError` if the target is not a `DRAFT`.
- `list_versions(session, tenant_id) -> list[TenantConfigRead]` — all versions
  for a tenant, newest first (for audit/history).

Reuses `core.exceptions.NotFoundError` / `ConflictError` and their existing
FastAPI handlers, consistent with `shared/tenant/service.py`.

## Validation (pydantic — the DB will not enforce JSON shape)

Because the payloads are `JSONB`, pydantic is the sole guardian. Validation runs
on **write** (`create_draft` validates `TenantConfigCreate`) and on **read**
(`get_active_config` builds `TenantConfigRead`, re-parsing the payloads), so a
hand-edited bad row cannot silently corrupt scoring.

- `Weights`: all five dimensions present; each ∈ [0,1]; sum ≈ 1.0 (tolerance
  1e-6, because exact float equality is unsafe).
- `Thresholds`: `hot`, `warm` ∈ [0,100]; `hot > warm` (a HOT threshold at or
  below WARM would break bucketing).
- `signals`: non-empty; each `dimension` is a valid `Dimension`; **all five
  dimensions appear at least once**; `id` unique within the version.

## Testing (TDD, follows existing pattern)

**Unit — no DB or network** (`tests/unit/test_tenant_config_schemas.py`, styled
after `test_tenant_schemas.py`):

- `Weights`: valid set accepted; sum ≠ 1.0 rejected; sum within tolerance
  accepted; out-of-range or missing dimension rejected.
- `Thresholds`: `hot > warm` accepted; `hot == warm` and `hot < warm` rejected;
  out-of-range rejected.
- `Signal` / `TenantConfigCreate`: empty signals rejected; duplicate signal `id`
  rejected; a missing dimension (only four represented) rejected; invalid
  `dimension` value rejected.
- `ConfigStatus` / `Dimension` enum membership is exactly the expected set.

**Integration — real Postgres** (`tests/integration/test_tenant_config_service.py`,
using the existing truncating `session` fixture and a `create_tenant` helper):

- `create_draft` assigns version 1, then version 2 on a second call after the
  first is approved; status is `DRAFT`.
- A second `create_draft` while a `DRAFT` exists → `ConflictError`
  (one-draft rule / partial unique index).
- `approve_version` on a draft → `ACTIVE`, `activated_at` set; a prior `ACTIVE`
  becomes `ARCHIVED` with `archived_at` set; `get_active_config` returns exactly
  the newly active one.
- Two `ACTIVE` versions for one tenant are impossible (partial unique index
  rejects it).
- `reject_version` → `REJECTED`; rejecting a non-draft → `ConflictError`.
- Rollback: `approve_version` on an `ARCHIVED` version re-activates it and
  archives the current; `approve_version` on a `REJECTED` version →
  `ConflictError`.
- `get_active_config` returns `None` for a tenant with no active version;
  `list_versions` returns all versions newest-first.

## Deferred (YAGNI) — explicit follow-ups

- **`TenantConfigActivated` event + Postgres `NOTIFY` cache invalidation.**
  When a new version is activated, `scoring` will need to flush its cached copy
  (ADR 0002). This signal is **not** built now — it lands with its first
  consumer (`scoring` / the persona cache), consistent with `shared/events`
  being contracts-only and `core/cache.py` deferred. `activate_version` performs
  only the atomic DB flip today.
- **`EVALUATION` status** — a draft-evaluation phase (test against historical
  leads before activation); add when there is data to evaluate against.
- **The in-process persona/config TTLCache** (`core/cache.py`) — built with its
  first consumer.
- **`archived_reason`** or distinguishing archive causes beyond the
  `REJECTED` status — add only if reporting needs it.

## Reference

- ADR 0001 — held-lead release on tenant activation is event-based
  (`TenantActivated`); first-config approval is the activation trigger.
- ADR 0002 — PersonaObject cache invalidation uses Postgres `NOTIFY`; the
  `tenant_config` activation signal will reuse this mechanism when `scoring`
  exists.
- ADR 0003 — deterministic scoring; the active `tenant_config` version is part
  of the pinned inputs.
- `shared/tenant` — the precedent for `shared/` module structure
  (`schemas.py` + `service.py` + internal `models.py`) and `StrEnum` usage.
- `shared/events` — the precedent for "contracts/delivery deferred until the
  first consumer."
- `docs/architecture.md` — the dependency rule and module boundaries.
