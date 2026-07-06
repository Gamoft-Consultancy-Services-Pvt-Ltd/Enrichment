# PAN-based KYB Design (replaces GST-OTP)

**Date:** 2026-06-19
**Status:** Approved (design) — supersedes `2026-06-15-gst-otp-kyb-design.md`
**Provider:** Sandbox (Quicko) — `POST /kyc/pan/verify`
**Branch:** fresh `feature/pan-kyb` off `00252d3` — the last commit *before* the
GST-OTP work began (Serper web-search + the full Phase 1 onboarding stack, with no
GST code). `main` is at Phase 0 and lacks the onboarding pipeline, so it is **not** a
valid base. The `feature/gst-otp-kyb` branch is abandoned, left unmerged on origin as
a record.

## Why this replaces GST-OTP

Surepass GST-OTP required a sales call to get credentials, and the OTP round-trip
added telecom cost and a multi-step state machine. Plan changed to **PAN
verification without OTP**, which self-serve providers (Sandbox, Cashfree, Attestr)
expose with immediate signup and no sales call. PAN verification proves the PAN
exists and (with Sandbox) that the applicant knows the registered name + date of
birth tied to it; the company-email gate (Auth0) carries the rest of the anti-abuse
weight.

This is an **anti-abuse ("first bucket") gate**, not compliance-grade KYB.

## Anti-abuse model

The gate is a **conjunction** — both must hold to onboard:

1. **Business-domain email (enforced at Auth0).** The login email's domain must not
   be a free/consumer provider (`gmail.com`, `yahoo.com`, `outlook.com`, …). Login
   is via that same company email, so controlling it is the proof of association.
2. **PAN identity match (Sandbox).** The submitted PAN must be **valid/active**, and
   the **name-as-per-PAN** and **date of birth / incorporation date** the applicant
   supplies must both match the registry. Accepts **individual *and* business** PANs —
   sole proprietors legitimately operate on a personal PAN (4th char `P`).

Together these raise the bar enough for first-bucket abuse deterrence. **No OTP
anywhere in this design.**

### Why a name + DOB match here (and why it doesn't break sole proprietors)

Sandbox's PAN API is a *match* API: it **requires** `name_as_per_pan` + `date_of_birth`
as inputs and returns match booleans (it does not hand back the registry name). We
gate on those matches (Option A) — otherwise we'd be collecting name + DOB only to
ignore them.

The earlier objection to name-matching was matching the registry name against the
**trade name** (which differs for proprietors). We avoid that entirely: we ask the
applicant for the name **exactly as printed on the PAN** (for a proprietor, their own
name; for a company, the registered name) and for the PAN's DOB/incorporation date,
and match *those*. A legitimate applicant knows their own PAN details, so this is
proprietor-safe. We still do **not** filter on entity type — individual PANs pass.

### Why the email domain can't be bound to the company

There is **no authoritative email-domain → legal-entity mapping** (Google Workspace
exposes no public ownership lookup; WHOIS/RDAP is GDPR-redacted; enrichment APIs are
best-effort guesses). So we do **not** try to match the email domain to the PAN; we
rely on the conjunction above.

## Login (Auth0 config — no app code)

The app only verifies Auth0-issued JWTs (`auth/token.py`), so login method is an
Auth0-tenant setting.

- **Enable passwordless email** (email OTP code — magic link isn't supported on
  New Universal Login; see the runbook) so any company mailbox works regardless
  of host — Google Workspace, GoDaddy/Microsoft 365, Zoho, etc.
  This also *is* the email-control proof. (Google-only social login would break
  every non-Google-hosted company mailbox.)

## Email-domain gate (Auth0 Post-Login Action — no app code)

Enforced entirely in Auth0, **not** in application code:

- A **Post-Login Action** inspects `event.user.email`, checks the domain against a
  **free-provider blocklist** (small, stable), and calls `api.access.deny(...)`.
  Free-email users never receive a token — stopped at the door.
- Because every token is Auth0-minted and signature-verified, a reliably-denying
  Action means no free-email token can exist; an app-side duplicate check is
  intentionally **omitted**.

**Deferred:** disposable-email-domain blocking (thousands of churning domains;
YAGNI v1). If added later it goes **app-side** as a testable data file, not Auth0.

## Data model (`shared/tenant`)

Fresh branch off `00252d3`, where `tenants` has **no KYB columns**, so a **single new
migration** adds (no OTP columns ever exist):

| Column | Type | Notes |
|---|---|---|
| `pan` | String, NOT NULL | the submitted PAN |
| `kyb_status` | String | mirrors `onboarding_status` storage; only `VERIFIED` is persisted by this flow |
| `kyb_company_data` | JSONB, nullable | the kept verification record: `name`, `category`, `status` |
| `kyb_verified_at` | DateTime, nullable | set at verification |

Schema changes (`TenantCreate`):

- **`pan: str`** — `field_validator` strips/uppercases and matches `^[A-Z]{5}[0-9]{4}[A-Z]$`.
- **`pan_holder_name: str`** — name exactly as per the PAN (min 2 chars).
- **`pan_dob: str`** — `DD/MM/YYYY` (DOB for individuals, incorporation date for
  companies), validated `^(0[1-9]|[12][0-9]|3[01])/(0[1-9]|1[0-2])/[0-9]{4}$`.
- **`consent: bool`** — must be `True`; the tenant explicitly consents to the PAN
  lookup. The `consent: "Y"` sent to Sandbox is thus a genuinely captured affirmation,
  not an unconditional hardcode. A missing/false value is a 422 at validation.

`pan_holder_name` / `pan_dob` / `consent` are verification inputs only (not persisted).
We persist the confirmed `name` (inside `kyb_company_data`) but **do not store the raw
DOB** (PII minimisation). `consent` is not persisted in v1.

- `TenantRead`: expose `pan` and `kyb_status`.
- `KybStatus` enum (`PENDING / VERIFIED / FAILED`) is retained for the column type
  and `TenantRead`. Only **VERIFIED** is ever written (verify-then-create means
  failures never produce a row); `PENDING`/`FAILED` remain for future admin use.

`kyb_status` stays a bare `String` column (not `SQLEnum`), matching `onboarding_status`.
Consumers must compare with `==`, not `is` (DB returns a plain `str`).

## PAN client (`clients/pan_client.py`)

Transport + parse only — the **gate policy lives in the kyb module**, not here.

```python
class PanCheck(TypedDict):
    category: str
    status: str        # "valid" when the PAN is active
    name_match: bool
    dob_match: bool

async def verify_pan(pan: str, name: str, dob: str) -> PanCheck
```

- **Mock-first**, gated by `pan_use_mock` (default `True`): returns a `PanCheck` with
  `status="valid"` + both matches `True` for any valid-format PAN, and a non-matching
  `PanCheck` (`status="invalid"`) for the sentinel `"AAAAA0000A"` — so onboarding runs
  end-to-end with no credentials.
- **Live path — Sandbox two-step (token then verify):**
  1. `POST {base}/authenticate` with headers `x-api-key`, `x-api-secret`,
     `x-api-version: 1.0.0` → `access_token` (a JWT, valid 24h).
  2. `POST {base}/kyc/pan/verify` with headers `Authorization: <token>` (**no `Bearer`
     prefix**), `Content-Type: application/json`, `x-api-key` (**no `x-api-version` on
     this call** — confirmed against the provider's verify curl), and body
     `{"@entity": "in.co.sandbox.kyc.pan_verification.request", "pan", "name_as_per_pan",
     "date_of_birth", "consent": "Y", "reason": "<≥20 chars>"}`.
  - Non-`200` on either call, a network error, **or an unexpected/unparseable body** →
    `ExternalServiceError` (auth/config/input/`503 Source Unavailable`/provider down —
    never silently "not verified"). Because this client runs synchronously in
    `/onboarding` and `app_error_handler` echoes `exc.message` to the caller, the
    exception carries a **fixed generic message**; the real cause (status, exception,
    key) is logged via structlog and never includes the api key/secret.
  - `200` → parse `data.{category, status, name_as_per_pan_match, date_of_birth_match}`
    into `PanCheck` (parsing is wrapped, so a renamed/missing key is a clean 502).
  - The token is minted **per verify call** (onboarding is low-volume); a 24h token
    cache is a deferred optimisation.
- Base URL: `https://test-api.sandbox.co.in` (sandbox) / `https://api.sandbox.co.in`
  (prod). The token-gated live test confirms the contract once credentials exist.

New config (`core/config.py`): `pan_api_key: str = ""`, `pan_api_secret: str = ""`,
`pan_base_url: str = ""` (Sandbox base URL), `pan_use_mock: bool = True`.

## KYB module (`modules/tenant_onboarding/kyb.py`)

Holds the **Option-A gate** so the API never imports `clients/` directly:

```python
async def verify_pan_kyb(pan: str, name: str, dob: str) -> dict[str, Any] | None
```

- Calls `pan_client.verify_pan`. **Verified iff** `status == "valid"` **and**
  `name_match` **and** `dob_match`. On success returns the record to persist:
  `{"name": name, "category": check["category"], "status": check["status"]}`. Otherwise
  returns `None`.

## Onboarding flow (`api/onboarding.py`)

`POST /onboarding` — synchronous, **verify-then-create** (no orphan rows):

1. If `user.tenant_id is not None`: load the existing tenant (`get_tenant`).
   - If its `onboarding_status == PENDING` (created but pipeline never started — the
     enqueue-failure case) → **re-enqueue `run_onboarding_pipeline` and return 200**
     (idempotent recovery; PAN is not re-verified, the tenant is already VERIFIED).
   - Otherwise (RUNNING/COMPLETE/FAILED) → `ConflictError` (genuine repeat).
2. `verify_pan_kyb(data.pan, data.pan_holder_name, data.pan_dob)`:
   - `None` → `UnprocessableError` (422). **Nothing is created.**
   - dict → continue.
3. `create_tenant(...)` persisting `pan`, `kyb_status=VERIFIED`,
   `kyb_company_data=<record>`, `kyb_verified_at=now`.
4. `set_user_tenant(session, user, tenant.id)`.
5. Enqueue `run_onboarding_pipeline`.
6. Return `TenantRead`.

A failed match is a 422 response; retry = call `/onboarding` again with corrected
name/DOB. The 422 message stays generic (it does not say which of name/DOB/PAN failed).
No OTP/resend/restart endpoints.

**Enqueue-failure recovery:** if step 5 fails after the tenant is created VERIFIED, the
tenant is left PENDING and the user gets a 502/500; the user simply retries
`/onboarding` and step 1's idempotent branch re-enqueues the pipeline. This closes the
former dead-end (where a retry returned 409 forever and needed admin reconciliation).
Re-enqueue is safe even if the first enqueue had silently succeeded: the pipeline's
`create_active` supersedes the prior config version rather than duplicating it.

## Service layer (`shared/tenant/service.py`)

`create_tenant(session, data, *, kyb_company_data: dict[str, Any] | None = None)`
persists `pan` and, when `kyb_company_data` is given, marks the tenant `VERIFIED` with
`kyb_verified_at = now`. No OTP helpers.

## Testing (TDD)

- **Unit:**
  - `pan_client` mock path — valid PAN → `status="valid"` + matches; sentinel → not valid.
  - `verify_pan_kyb` gate — verified only when status valid AND both matches; returns
    `None` on a name/DOB/status failure (mock the client).
  - Schema validation — `pan`, `pan_holder_name`, `pan_dob` accept/reject correctly.
- **Integration (real Postgres + Redis):**
  - business flow — valid PAN + matching name/DOB → tenant `VERIFIED` + job enqueued.
  - failed match → 422, **no tenant row created**.
  - already-onboarded user → `ConflictError`.
- **Live (token-gated):** `tests/integration/test_pan_live.py`, skipped unless
  `PAN_API_KEY` is set — confirms the Sandbox two-step contract end-to-end.

The Auth0 Post-Login Action (email gate) and passwordless login are **not** covered by
the pytest suite by design — they live in Auth0 config.

## Out of scope / deferred

- **Disposable-email-domain blocking** — app-side data file, deferred (YAGNI v1).
- **Domain-enrichment (Clearbit/PDL)** as a soft manual-review risk signal — not a gate.
- **Entity-type filtering** (rejecting individual PANs) — rejected (breaks sole proprietors).
- **Trade name + registered address / GSTIN verification** — considered (PAN/Sandbox
  return neither; those live only in the GST registry). Rejected for v1: GSTIN-verify
  would exclude tenants not registered for GST, and PAN is universal. Revisit only if
  auto-populating a richer company profile becomes a requirement.
- **24h Sandbox token cache** — minted per call for now; optimise if volume warrants.
- **Retry / circuit-breaker for provider outage** — the PAN check hard-fails (502) if
  Sandbox is down; no retry or queue-for-later. Acceptable at onboarding volume; revisit
  only if provider flakiness becomes real. (The VERIFIED-but-enqueue-failed window is no
  longer deferred — it is handled by the idempotent `/onboarding` recovery above.)

## Migration note

The base (`00252d3`) does **not** contain the GST migration (`4d10d1fcaa7e_add_kyb_to_tenants`)
— that lived only on the abandoned GST branch. The new migration chains off the base's
head, `8fccb46_add_website_url_onboarding_status_to_tenants`, and adds `pan` + the three
kyb columns from scratch. The `gstin` column never exists here.
