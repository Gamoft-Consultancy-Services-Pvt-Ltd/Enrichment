# Tenant Onboarding — Agent Pipeline Design

**Date:** 2026-06-08
**Status:** Approved (design); implementation pending
**Scope:** `modules/tenant_onboarding` + supporting changes to `shared/tenant`, `shared/tenant_config`, `clients/`, `core/queue.py`, `api/onboarding.py`, `workers/`

---

## Purpose

After a tenant submits their company info via `POST /onboarding`, a fully automated three-agent pipeline runs in the background. It fetches the tenant's website, then calls three Sonnet agents in sequence — Persona → ICP → Signals — to build a complete `tenant_config`. When the pipeline finishes, the config goes live immediately (`ACTIVE`) and the tenant's status transitions to `ACTIVE`. No human approval required.

---

## Flow

```
POST /onboarding (website_url + company info)
  → Create Tenant (CREATED, onboarding_status=PENDING)
  → Enqueue ARQ job

ARQ job:
  1. Set onboarding_status = RUNNING
  2. Fetch website via httpx → strip HTML to plain text (BeautifulSoup)
  3. Persona Agent  → business_profile dict
  4. ICP Agent      → icp dict
  5. Signals Agent  → signals list + weights + thresholds
  6. create_active(tenant_id, TenantConfigCreate(...))   ← shared/tenant_config
  7. activate_tenant(tenant_id)                          ← CREATED → ACTIVE
  8. Construct TenantActivated event (delivery deferred — no bus yet, ADR 0001)
  9. Set onboarding_status = COMPLETE

On any failure → set onboarding_status = FAILED
```

The tenant checks progress via `GET /me` — `onboarding_status` tells the frontend whether to show a spinner, success state, or retry prompt.

---

## Schema Changes

### `shared/tenant/schemas.py`

Add `OnboardingStatus` enum (lives here, not in `modules/`, because `TenantRead` must import it and `shared/` cannot import from `modules/`):

```python
class OnboardingStatus(StrEnum):
    PENDING  = "PENDING"   # job queued, not yet picked up
    RUNNING  = "RUNNING"   # pipeline executing
    COMPLETE = "COMPLETE"  # tenant_config ACTIVE, tenant ACTIVE
    FAILED   = "FAILED"    # pipeline crashed — tenant can retry
```

`TenantCreate` gains `website_url` (required; validated as `AnyHttpUrl`):

```python
class TenantCreate(BaseModel):
    company_name: str
    primary_contact_name: str
    primary_contact_email: EmailStr
    business_type: BusinessType
    website_url: AnyHttpUrl          # new — the agents' source of truth
    timezone: str = "UTC"
    language_preference: str = "en"
```

`TenantRead` gains `website_url` and `onboarding_status`:

```python
class TenantRead(BaseModel):
    ...
    website_url: str
    onboarding_status: OnboardingStatus   # new — frontend polls this
    ...
```

### `shared/tenant/models.py`

Two new columns on `tenants`:

```
website_url          VARCHAR     NOT NULL
onboarding_status    VARCHAR     NOT NULL  DEFAULT 'PENDING'
```

### `shared/tenant/service.py`

Add `activate_tenant(session, tenant_id) -> Tenant` — transitions `CREATED → ACTIVE`, sets `activated_at`.

### `shared/tenant_config/schemas.py`

Simplify `ConfigStatus` — remove `DRAFT` and `REJECTED` (no human approval step):

```python
class ConfigStatus(StrEnum):
    ACTIVE   = "ACTIVE"
    ARCHIVED = "ARCHIVED"
```

### `shared/tenant_config/service.py`

Remove: `create_draft`, `approve_version`, `reject_version`.
Keep: `get_active_config`, `list_versions`.
Add: `create_active(session, tenant_id, data: TenantConfigCreate) -> TenantConfig` — archives any existing `ACTIVE` version for the tenant, then inserts the new one as `ACTIVE`.

---

## File Structure

```
modules/tenant_onboarding/
├── __init__.py
├── schemas.py        ← module-level re-exports (nothing new beyond shared/tenant)
├── service.py        ← update_onboarding_status(session, tenant_id, status)
├── pipeline.py       ← ARQ job entry point; orchestrates fetch + 3 agents
└── agents/
    ├── __init__.py
    ├── persona.py    ← website_text + tenant info → business_profile dict
    ├── icp.py        ← business_profile → icp dict
    └── signals.py    ← business_profile + icp → signals, weights, thresholds

clients/
└── anthropic.py      ← thin async wrapper around the Anthropic Python SDK

core/
└── queue.py          ← implement ARQ Redis pool (first real consumer)
```

---

## Agent Design

All agents call Anthropic Sonnet at **temperature 0** (deterministic per ADR 0003) and use **tool use** to enforce structured JSON output — no free-text parsing.

### Persona Agent (`agents/persona.py`)

```
Input:  company_name: str, business_type: BusinessType, website_text: str
Output: business_profile: dict[str, Any]
        e.g. { industry, target_market, products_services, company_size, geography, value_proposition }
```

Prompt instructs the model to extract factual business details from the website text, grounded only in what the website says.

### ICP Agent (`agents/icp.py`)

```
Input:  business_profile: dict[str, Any]
Output: icp: dict[str, Any]
        e.g. { buyer_role, company_size, industry_vertical, pain_points, budget_range, decision_timeline }
```

Prompt derives the ideal customer from the business profile — who would benefit most from this company's offering.

### Signals Agent (`agents/signals.py`)

```
Input:  business_profile: dict[str, Any], icp: dict[str, Any]
Output: signals: list[Signal], weights: Weights, thresholds: Thresholds
```

Output must satisfy `TenantConfigCreate` validation:
- All five `Dimension` values covered by at least one signal
- Signal IDs unique within the version
- `weights` sum to 1.0
- `thresholds.hot > thresholds.warm`

Default thresholds if the model doesn't have strong signal: `hot=80`, `warm=55`.

---

## `clients/anthropic.py`

Thin async wrapper — the only file that imports `anthropic`:

```python
async def complete(
    messages: list[dict],
    tools: list[dict],
    model: str,
    temperature: float = 0.0,
) -> dict:
    """Call the Anthropic API and return the first tool_use block's input dict."""
```

Reads `ANTHROPIC_API_KEY` from `core.config.Settings`. Raises `ClientError` (a new subclass of the existing exception hierarchy) on API failure.

---

## `core/queue.py`

Implements the ARQ Redis pool (first real consumer — previously a stub):

```python
async def get_arq_pool() -> ArqRedis:
    """Return a connected ARQ Redis pool, reading REDIS_URL from settings."""
```

`POST /onboarding` calls `await pool.enqueue_job("run_onboarding_pipeline", tenant_id=str(tenant.id))`.

---

## API Changes

### `POST /onboarding` (`api/onboarding.py`)

Existing endpoint. Changes:
- `TenantCreate` now includes `website_url` — no endpoint signature change needed.
- After `set_user_tenant`, enqueue the ARQ pipeline job.

### `GET /me` (`api/me.py`)

No change — `TenantRead` already returned here; gains `website_url` + `onboarding_status` automatically via the schema update.

---

## `workers/`

Register the pipeline as an ARQ job:

```python
# workers/jobs/onboarding.py
async def run_onboarding_pipeline(ctx: dict, tenant_id: str) -> None:
    """ARQ job: run the full persona→ICP→signals pipeline for a tenant."""
```

---

## Error Handling

| Situation | Behaviour |
|---|---|
| Website unreachable / timeout / non-200 | `onboarding_status = FAILED` |
| HTML too large or unparseable | `onboarding_status = FAILED` |
| Anthropic API error or malformed tool output | `onboarding_status = FAILED` |
| `TenantConfigCreate` validation fails | `onboarding_status = FAILED` |
| DB write fails | `onboarding_status = FAILED` |

In all cases the exception is re-raised so ARQ logs it. The tenant sees `FAILED` and can re-submit.

---

## Testing

### Unit tests

- **Each agent** — mock `clients/anthropic.py`; assert correct prompt inputs and that output parsing produces a valid typed object.
- **Pipeline** — mock all three agents + `shared/tenant_config.create_active` + `shared/tenant.activate_tenant`; assert correct sequencing, status transitions (`PENDING → RUNNING → COMPLETE`), and `FAILED` on exception.

### Integration tests

- **`POST /onboarding`** — real DB; verify `Tenant` created with `onboarding_status=PENDING` and `website_url` stored; verify ARQ job enqueued.
- **`GET /me`** — verify `onboarding_status` is present in the response.

### Existing tests to update

- `tests/unit/test_tenant_config_schemas.py` — remove `DRAFT`/`REJECTED` cases.
- `tests/integration/test_tenant_config_service.py` — remove `create_draft`, `approve_version`, `reject_version` tests; add `create_active` test.

---

## Deferred

- **`TenantActivated` event delivery** — event object is constructed in the pipeline but not published. Delivery lands when `modules/orchestration` is built (ADR 0001).
- **Retry UI** — re-triggering a `FAILED` onboarding (same endpoint, same body) is architecturally valid but not explicitly wired as a dedicated retry endpoint.
- **Website content size limits** — no hard cap on fetched HTML size for now; a future hardening pass can add a byte limit.

---

## Consequences

- `shared/tenant_config` is simplified: two statuses instead of four, no approval workflow.
- `core/queue.py` gains its first real implementation.
- `clients/anthropic.py` is the first real external client.
- The `tenants` table gains two columns (one migration).
- Existing `tenant_config` tests need updating to remove DRAFT/REJECTED coverage.
