# GST-OTP KYB Gate — Design

**Date:** 2026-06-15
**Status:** Approved (design)
**Module:** `modules/tenant_onboarding` (+ `clients/surepass_client`, `shared/tenant`)

## Problem & goal

Today any authenticated user can create a tenant via `POST /onboarding` by
supplying business details and a `website_url`; the onboarding pipeline is
enqueued immediately. There is no verification that the signer represents a real
business. This is an **anti-abuse** gap: fake or throwaway signups consume Groq
and external-API budget building scoring configs for businesses that don't exist.

**Goal:** require every tenant to prove control of an **active GSTIN** before the
onboarding pipeline runs, using Surepass's **GST verification with OTP**. OTP
proves control because the one-time code is delivered to the contact the business
registered with the GST authority (GSTN) — not merely that a valid-looking GSTIN
was typed.

This is a server-enforced gate. The verification decision is made and recorded by
the backend, never trusted from the client.

## Decisions (locked during brainstorming)

1. **Flow shape — Option A.** The tenant row is created up front in a
   `KYB_PENDING` state. A two-step OTP exchange runs against Surepass. The
   onboarding pipeline is enqueued **only** once KYB reaches `VERIFIED`.
   (Rejected: B — tenant created only after OTP, holds details in Redis; C —
   client-side verification, which is bypassable and therefore not a real gate.)
2. **State modeling — dedicated enum.** A new `KybStatus` field on the tenant,
   separate from `TenantStatus` (account lifecycle) and `OnboardingStatus`
   (pipeline progress). Keeps the gate check and `FAILED` semantics unambiguous.
3. **Failure/retry rules.** 3 verify attempts → `FAILED`; a `FAILED` tenant may
   restart KYB (resets counters, fresh OTP); resend allowed, capped at 3 per
   onboarding; abandonment cleanup (sweeper for stale `KYB_PENDING` rows)
   **deferred**.
4. **Pass criteria — OTP alone.** A successful OTP passes KYB. The verified
   company data is **stored** but name-matching against the typed company name /
   website is **deferred** (legal-vs-trade-name mismatches are common in India and
   would cause false rejections).
5. **Scope — uniform.** All tenants are gated regardless of `B2B`/`B2C`. No
   self-selectable exemption.

## Architecture & boundaries

Dependency rule preserved: `api → modules → shared → clients → core`.

| Layer | File | Responsibility |
|---|---|---|
| `clients` | `surepass_client.py` (stub → built) | Only file that talks to Surepass. `send_gst_otp(gstin) -> txn_ref`, `verify_gst_otp(txn_ref, otp) -> CompanyData`. Imports `core` config only. Mirrors `groq_client.py`. |
| `shared` | `tenant/service.py` | Pure DB state transitions (no network): store txn ref, increment counters, `mark_kyb_verified`, `mark_kyb_failed`. |
| `shared` | `tenant/schemas.py` | New `KybStatus` enum; `gstin` on `TenantCreate`; `kyb_status` on `TenantRead`. |
| `modules` | `tenant_onboarding/kyb.py` (new) | Orchestration / business rules: composes Surepass client + tenant service; owns attempt/resend caps and pass/fail logic. Does **not** enqueue. New public surface of the module. |
| `api` | `onboarding.py` | Thin endpoints. Enqueues the pipeline on `VERIFIED` (mirrors current enqueue-after-create pattern). |
| `core` | `config.py` | New settings: `surepass_api_key`, `surepass_base_url`. |

Orchestration sits in `modules/tenant_onboarding` (not `api/`, to keep business
rules out of the HTTP layer; not `shared/`, to keep `shared/` network-free).

## Data model — new columns on `tenants`

- `gstin: str` — required on `TenantCreate`, validated by GSTIN-format regex
  (15 chars: 2-digit state + 10-char PAN + entity + `Z` + checksum). Malformed
  input → `422` before any row is created.
- `kyb_status: KybStatus` — new `StrEnum` in `shared/tenant/schemas.py`:
  `PENDING → VERIFIED / FAILED`. Default `PENDING`.
- `kyb_company_data: JSONB | null` — verified record from Surepass (legal name,
  trade name, address, status), stored on success.
- `kyb_txn_ref: str | null` — transient Surepass transaction reference held
  between send and verify; cleared on `VERIFIED`/`FAILED`.
- `kyb_attempts: int` (default 0), `kyb_resends: int` (default 0) — caps.
- `kyb_verified_at: datetime | null`.

One Alembic migration adds these columns.

## API surface & flow

```
POST /onboarding {business details + gstin}
  → validate GSTIN format (422 if bad)
  → create_tenant (kyb_status=PENDING) → set_user_tenant
  → kyb.start_verification: surepass.send_gst_otp → store txn_ref
  → 200 TenantRead (kyb_status=PENDING)        # pipeline NOT enqueued

POST /onboarding/verify-otp {otp}
  → kyb.submit_otp → surepass.verify_gst_otp(txn_ref, otp)
       success    → store company_data, kyb_status=VERIFIED, clear txn_ref,
                    kyb_verified_at=now
                  → api enqueues run_onboarding_pipeline
       wrong otp  → kyb_attempts++ ; at 3 → kyb_status=FAILED, clear txn_ref

POST /onboarding/resend-otp
  → if kyb_resends >= 3 → error; else surepass.send_gst_otp, kyb_resends++

POST /onboarding/restart-kyb {gstin?}
  → only if kyb_status == FAILED
  → reset kyb_status=PENDING, kyb_attempts=0, kyb_resends=0,
    optional corrected gstin, fresh OTP
```

`GET /me` exposes `kyb_status` alongside `onboarding_status` for polling.

The onboarding pipeline (`pipeline.py`) is **unchanged** for this slice. Feeding
the verified legal name into the Persona agent is a deferred follow-up (the data
is stored now; wiring is separate).

## Error handling

- **Malformed GSTIN** → `422` at `POST /onboarding`; no tenant created.
- **Surepass transport/API error on send** → tenant stays `PENDING`; endpoint
  returns an error; user retries via `resend-otp`.
- **Wrong OTP** → counted; `FAILED` at 3 attempts; `restart-kyb` revives it.
- **Abandoned `KYB_PENDING`** → left in place (cleanup sweeper deferred). This is
  the accepted cost of Option A (unverified rows can accumulate).
- **Audit** → `shared/audit/` is still an empty stub, so the outcome is recorded
  on the tenant row (`kyb_verified_at` + `kyb_company_data`). Formal audit-log
  entries are deferred until that module exists.

## Testing (TDD)

- **Unit (no DB/network):** GSTIN regex validation; `surepass_client` with mocked
  httpx (send + verify, success/failure); `kyb.py` orchestration with mocked
  client + service (attempt cap → `FAILED`, resend cap, verify → `VERIFIED`);
  state-transition helpers.
- **Integration (real Postgres):** the four endpoints — happy path (onboard →
  verify → pipeline enqueued) and the 3-strikes-then-restart path.

## Out of scope (explicitly deferred)

- Name-matching the verified company against typed company name / website.
- Sweeper to expire stale `KYB_PENDING` tenants.
- Feeding verified legal name into the Persona agent.
- Formal audit-log entries (await `shared/audit`).
- UBO / sanctions / PEP / adverse-media screening (this is anti-abuse KYB, not
  compliance-grade KYB).
