# Onboarding — collect tenant business info — design

**Date:** 2026-06-04
**Status:** Approved (design); implementation pending
**Scope:** `auth/` + `api/` + `shared/tenant` (reuse); builds on the Auth0 authentication slice

## Purpose

Deliver the **"authenticate → fill in your business info → you're set up"** loop as a
backend slice, demoable entirely through FastAPI's `/docs`. After a user logs in
via Auth0, they have an identity but no company profile; this slice lets them
submit their business details, which creates a `Tenant` and links it to their
`User`.

This is the minimal, real version of self-service onboarding. It is **not** the
full `modules/tenant_onboarding` (the Sonnet agent chain that builds
persona/ICP/scoring prompt and activates the tenant) — that remains deferred.

## Demo flow (all via `/docs`)

1. Log in via Auth0 → token with role `TENANT` and **no** `tenant_id` claim
   (the tenant does not exist yet).
2. `GET /me` → creates the `User` with `tenant_id = null`. The null signals
   "not onboarded yet."
3. `POST /onboarding` with the business fields → creates the `Tenant`, links it
   to the user, returns the created business profile.
4. `GET /me` again → now shows the populated `tenant_id`.

## Source of truth (key decision)

**Our database owns the user→tenant link** (not Auth0). For self-service signup
the tenant is born in our system *after* login, so Auth0's token never carries
it. We therefore do **not** write the tenant back to Auth0 (no Management API).

Conceptual split this reflects:
- **Auth0 owns identity** — who you are (`sub`, `email`, how you logged in).
- **Our DB owns business relationships** — which tenant you belong to, your
  leads, your scoring.

Writing the tenant back to Auth0 (Management API, so the claim stays
authoritative) is **deferred** until a future need appears (e.g. an enterprise
admin assigning users to tenants inside Auth0).

## Changes

### 1. Fix `get_or_create_user` (`auth/service.py`)

Today, on every login it refreshes `user.tenant_id = principal.tenant_id`. For an
onboarded user whose token has no `tenant_id` claim, this would **null out** the
link. Fix: **only overwrite `tenant_id` when the token actually carries one**;
otherwise preserve the existing DB value.

```python
if user is None:
    user = User(
        auth0_sub=principal.subject,
        email=principal.email,
        role=principal.role,
        tenant_id=principal.tenant_id,   # may be None on first login
    )
    session.add(user)
else:
    user.email = principal.email
    user.role = principal.role
    if principal.tenant_id is not None:        # don't wipe a DB-owned link
        user.tenant_id = principal.tenant_id
```

`email` and `role` keep refreshing from the token as before (`role` is always
present in the token). Only `tenant_id` becomes DB-owned-once-set.

### 2. Link helper (`auth/service.py`)

A small public function that sets and persists the link (keeps the endpoint
declarative and is independently testable):

```python
async def set_user_tenant(session: AsyncSession, user: User, tenant_id: UUID) -> User:
    """Link a user to a tenant and persist it."""
    user.tenant_id = tenant_id
    await session.commit()
    await session.refresh(user)
    return user
```

### 3. Onboarding endpoint (`api/onboarding.py`)

```python
POST /onboarding   # requires get_current_user
```

- **Request body:** the existing `shared.tenant.schemas.TenantCreate`
  (`company_name`, `primary_contact_name`, `primary_contact_email`,
  `business_type`, `timezone`, `language_preference`). No new schema.
- **Response:** `shared.tenant.schemas.TenantRead`.
- **Logic:**
  1. If `user.tenant_id` is already set → raise `ConflictError` (HTTP 409,
     "already onboarded").
  2. `tenant = await create_tenant(session, data)` (existing `shared/tenant`
     service; tenant is created in `CREATED` status).
  3. `await set_user_tenant(session, user, tenant.id)`.
  4. Return `TenantRead.model_validate(tenant)`.
- Wired into `main.py` alongside `/me`.

**Boundaries:** the endpoint orchestrates; `shared/tenant` owns tenant creation;
`auth` owns the user mutation. (`api → auth`/`shared` only — dependency rule
intact.)

**Tenant status:** stays `CREATED` (i.e. "onboarding"). Activation
(`CREATED → ACTIVE`) and the persona/ICP/prompt agent chain belong to the
deferred `modules/tenant_onboarding`.

**Known acceptable edge:** `create_tenant` commits before linking, so a failure
between the two steps could leave an orphan `Tenant`. Acceptable for this slice;
the future onboarding module can wrap both in one transaction.

### 4. `/me` unchanged

`UserRead` already includes `tenant_id`, so after onboarding `GET /me` shows the
populated link with no change. (A richer `/me` that embeds full tenant details is
deferred — `tenant_id` is enough to demonstrate the loop.)

## Error handling

| Situation | Response |
|---|---|
| No/invalid bearer token | 401 (`AuthenticationError`, existing handler) |
| User already has a tenant | 409 (`ConflictError`, existing handler) |
| Missing/invalid business fields (bad email, unknown business_type) | 422 (FastAPI/pydantic validation, automatic) |

## Testing (TDD; real Postgres)

### Integration — `auth.service`
- **No-wipe:** create a user linked to a tenant, then call `get_or_create_user`
  again with a principal that has `tenant_id = None` → the link is **preserved**.
- **`set_user_tenant`:** links a user to a tenant and persists it.

### Integration — `POST /onboarding` (`TestClient`, `verify_token` stubbed as in `test_me_endpoint`)
- Authenticated `TENANT` user with no tenant → `POST /onboarding` with valid
  business info → **200**, returns the business profile, and the user's
  `tenant_id` is set.
- Second `POST /onboarding` for the same user → **409**.
- `GET /me` after onboarding → shows the populated `tenant_id`.
- No token → **401**.

## Consequences & follow-ups

- Completes the demoable "log in → onboard → you have a company profile" loop
  without a frontend.
- **Deferred:** the frontend onboarding form (separate repo); the full
  `modules/tenant_onboarding` (agent chain + activation); writing `tenant_id`
  back to Auth0; a richer `/me` embedding tenant details.
- Establishes that **our DB owns the user→tenant relationship** for self-service
  signup — a decision future onboarding/authorization work builds on.

## Reference

- `auth/service.py`, `auth/dependencies.py`, `auth/models.py` — the auth slice
  this extends.
- `shared/tenant/service.py` (`create_tenant`), `shared/tenant/schemas.py`
  (`TenantCreate`, `TenantRead`).
- `docs/superpowers/specs/2026-06-04-auth-authentication-design.md` — the
  authentication slice.
