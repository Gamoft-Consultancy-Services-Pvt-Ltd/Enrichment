# PAN-based KYB Design (replaces GST-OTP)

**Date:** 2026-06-19
**Status:** Approved (design) — supersedes `2026-06-15-gst-otp-kyb-design.md`
**Branch:** fresh `feature/pan-kyb` off `00252d3` — the last commit *before* the
GST-OTP work began (Serper web-search + the full Phase 1 onboarding stack, with no
GST code). `main` is at Phase 0 and lacks the onboarding pipeline, so it is **not** a
valid base. The `feature/gst-otp-kyb` branch is abandoned, left unmerged on origin as
a record.

## Why this replaces GST-OTP

Surepass GST-OTP required a sales call to get credentials, and the OTP round-trip
added telecom cost and a multi-step state machine. Plan changed to **PAN
verification without OTP**, which providers expose via self-serve signup and free
trial credits (Cashfree, Sandbox/Quicko, Attestr — no sales call). PAN-without-OTP
verifies that an entity is *real* but **not** that the applicant controls it, so the
anti-abuse weight shifts onto an enforced company-email gate.

This is an **anti-abuse ("first bucket") gate**, not compliance-grade KYB.

## Anti-abuse model

The gate is a **conjunction** — both must hold to onboard:

1. **Business-domain email (enforced at Auth0).** The login email's domain must not
   be a free/consumer provider (`gmail.com`, `yahoo.com`, `outlook.com`, …). Login
   is via that same company email, so controlling it is the proof of association.
2. **Real PAN (existence-only).** The submitted PAN verifies against NSDL. Accepts
   **individual *and* business** PANs — sole proprietors legitimately operate on a
   personal PAN (4th char `P`). No entity-type filter, no name match.

Neither alone is strong; together they raise the bar enough for first-bucket abuse
deterrence. **No OTP anywhere in this design.**

### Why not stricter PAN checks

- **Entity-type filter (reject individual PANs):** rejected — locks out sole
  proprietors / freelancers / single-person consultancies who have no company PAN.
- **Name match (PAN registry name vs `company_name`):** rejected — for a sole
  proprietor the PAN name is the person's name, not the trading name, so it would
  false-reject exactly the users it should admit.

### Why the email domain can't be bound to the company

There is **no authoritative email-domain → legal-entity mapping**. Google Workspace
exposes no public "who owns this domain" lookup; WHOIS/RDAP is GDPR-redacted;
domain-enrichment APIs (Clearbit/PDL) are best-effort guesses, not proof. So we do
**not** attempt to match the email domain against the PAN/company name — we rely on
the conjunction (controls a business domain AND supplied a real PAN).

## Login (Auth0 config — no app code)

The app only verifies Auth0-issued JWTs (`auth/token.py`), so login method is an
Auth0-tenant setting, not app code.

- **Enable passwordless email** (magic link or email OTP) so any company mailbox
  works regardless of host — Google Workspace, GoDaddy/Microsoft 365, Zoho, etc.
  This also *is* the email-control proof. (Google-only social login would break
  every non-Google-hosted company mailbox, e.g. GoDaddy/M365 — hence passwordless.)

## Email-domain gate (Auth0 Post-Login Action — no app code)

Enforced entirely in Auth0, **not** in application code:

- A **Post-Login Action** inspects `event.user.email`, checks the domain against a
  **free-provider blocklist** (small, stable, ~dozens of domains), and calls
  `api.access.deny(...)` to reject the login. Free-email users never receive a
  token — stopped at the door, not at `/onboarding`.
- Because every token is Auth0-minted and signature-verified, a reliably-denying
  Action means no free-email token can exist; an app-side duplicate check is
  redundant for security and is intentionally **omitted**.
- (Auth0's built-in "allowed email domains" is an allowlist for known enterprise
  domains — not usable for blocking a list of free providers — so this must be an
  Action.)

**Accepted trade-off:** this rule and its blocklist live in Auth0, outside the repo
and outside the pytest/TDD/code-review workflow. Accepted for the free-provider
list because it is small and stable.

**Deferred:** disposable-email-domain blocking. Those lists are thousands of
domains and churn constantly — awkward inside an Action and YAGNI for v1. If added
later, that one piece would go **app-side** (data-file-shaped and testable), not in
Auth0.

## Data model (`shared/tenant`)

Fresh branch off `main`, where `tenants` has **no KYB columns**, so a **single new
migration** adds (no OTP columns ever exist):

| Column | Type | Notes |
|---|---|---|
| `pan` | String, NOT NULL | the submitted PAN |
| `kyb_status` | String | mirrors `onboarding_status` storage; only `VERIFIED` is persisted by this flow |
| `kyb_company_data` | JSONB, nullable | NSDL response (name, category) |
| `kyb_verified_at` | DateTime, nullable | set at verification |

Schema changes:

- `TenantCreate`: **replace `gstin` with `pan`** — `pan: str` with a `field_validator`
  that strips/uppercases and matches `^[A-Z]{5}[0-9]{4}[A-Z]$`, raising on mismatch.
- `TenantRead`: expose `pan` and `kyb_status`.
- `KybStatus` enum (`PENDING / VERIFIED / FAILED`) is retained for the column type
  and `TenantRead`. In this flow only **VERIFIED** is ever written (verify-then-create
  means failures never produce a row); `PENDING`/`FAILED` remain available for future
  admin/suspension use.

`kyb_status` stays a bare `String` column (not `SQLEnum`), matching `onboarding_status`.
Consumers must compare with `==`, not `is` (DB returns a plain `str`).

## PAN client (`clients/pan_client.py`)

Single public function:

```python
async def verify_pan(pan: str) -> PanData | None
```

- `PanData` is a `TypedDict` (at least `name`, `category`). Convert to `dict[str, Any]`
  before persisting (mypy strict: TypedDict is not assignable to `dict[str, Any]`).
- **Mock-first**, gated by `pan_use_mock` (default `True`): returns mock `PanData` for
  a valid-format PAN, and `None` for a sentinel "not found" PAN, so onboarding runs
  end-to-end with no provider credentials.
- **Live path — Cashfree `POST /verification/pan`** (the chosen provider; self-serve,
  OTP-free, returns the registered name from the PAN alone). Send `{"pan": ...}` with
  the two `x-client-*` auth headers; map the response:
  - non-`200` → raise `ExternalServiceError`. For Cashfree a 4xx/5xx is *our* problem
    (bad config, auth, IP allowlist, insufficient balance, rate-limit, provider down) —
    **never** "PAN invalid".
  - `200` with `valid: false` or `pan_status != "VALID"` → return `None` (PAN does not
    exist / deactivated). **Note:** a non-existent PAN is reported as `200 + valid:false`,
    *not* a 4xx — this differs from a naïve "4xx = not found".
  - `200` with `valid: true` → `PanData(name=registered_name, category=type)`.
- The contract above is confirmed against Cashfree's published docs; the token-gated
  live test (below) verifies it end-to-end once credentials exist.

New config (`core/config.py`): `pan_client_id: str = ""`, `pan_client_secret: str = ""`
(Cashfree uses two header secrets, not a single bearer key), `pan_base_url: str = ""`
(`https://sandbox.cashfree.com` for sandbox, `https://api.cashfree.com` for prod),
`pan_use_mock: bool = True`.

Provider is **Cashfree** (decided after comparing Cashfree / Sandbox / Attestr / ClearTax:
only Cashfree is both self-serve *and* a lookup model that returns the registered name
from the PAN alone — Sandbox and Attestr-basic require name+DOB+consent and return only
match booleans; ClearTax is enterprise/sales-led).

## Onboarding flow (`api/onboarding.py`)

`POST /onboarding` — fully **synchronous, verify-then-create** (no orphan-tenant rows):

1. If `user.tenant_id is not None` → `ConflictError`.
2. `verify_pan(data.pan)`:
   - `None` → reject with a 4xx (PAN not verified). **Nothing is created.**
   - `PanData` → continue.
3. `create_tenant(...)` persisting `pan`, `kyb_status=VERIFIED`,
   `kyb_company_data=dict(pan_data)`, `kyb_verified_at=now`.
4. `set_user_tenant(session, user, tenant.id)`.
5. Enqueue `run_onboarding_pipeline` via the ARQ pool.
6. Return `TenantRead`.

**Removed endpoints:** `POST /onboarding/verify-otp`, `/onboarding/resend-otp`,
`/onboarding/restart-kyb`. A failed PAN is a 4xx response; retry = call `/onboarding`
again. The email-domain gate is not an endpoint concern (handled at Auth0).

**Residual window (documented, not built):** if enqueue fails after the tenant is
created VERIFIED, the pipeline never starts and the endpoint can't self-recover it —
needs admin reconciliation. Smaller than the GST design because verify-then-create
removes the orphan-tenant window.

## Service layer (`shared/tenant/service.py`)

- `create_tenant` persists `pan` and the kyb fields directly (tenant is born VERIFIED).
- **Drop** all OTP service helpers from the GST design (`store_kyb_txn`,
  `mark_kyb_verified`, `bump_kyb_attempts`, `bump_kyb_resends`, `mark_kyb_failed`,
  `reset_kyb`) — none have a PAN analog.

## Testing (TDD)

- **Unit:**
  - `pan_client` mock path — valid PAN → `PanData`; sentinel PAN → `None`; (live-path
    error mapping covered by construction, exercised live below).
  - PAN schema validation — accepts/normalizes valid PAN, rejects malformed, requires
    `pan`.
- **Integration (real Postgres + Redis):**
  - business flow — valid PAN → tenant persisted `VERIFIED` + job enqueued.
  - invalid PAN → 4xx, **no tenant row created**.
  - already-onboarded user → `ConflictError`.
- **Live (token-gated):** `tests/integration/test_pan_live.py`, skipped unless
  `PAN_CLIENT_ID`/`PAN_CLIENT_SECRET` are set — run once to confirm the Cashfree
  contract end-to-end.

The Auth0 Post-Login Action (email gate) and passwordless login are **not** covered by
the pytest suite by design — they live in Auth0 config.

## Out of scope / deferred

- **Disposable-email-domain blocking** — app-side data file, deferred (YAGNI v1).
- **Domain-enrichment (Clearbit/PDL)** as a soft manual-review risk signal — not a gate.
- **PAN name-matching / entity-type filtering** — rejected (breaks sole proprietors).
- **Trade name + registered address / GSTIN verification** — considered (PAN returns
  only the legal name, no trade name or address; those live only in the GST registry).
  Rejected for v1: GSTIN-verify would exclude tenants not registered for GST, and PAN
  is universal. Revisit only if auto-populating a richer company profile becomes a
  requirement — at which point GSTIN-verify (also OTP-free, also Cashfree) is the path.
- **Admin reconciliation** for the VERIFIED-but-enqueue-failed window.

## Migration note

The base (`00252d3`) does **not** contain the GST migration (`4d10d1fcaa7e_add_kyb_to_tenants`)
— that lived only on the abandoned GST branch. The new migration chains off the base's
head, `8fccb46_add_website_url_onboarding_status_to_tenants`, and adds `pan` + the three
kyb columns from scratch. The `gstin` column never exists here.
