# auth — Auth0 authentication slice — design

**Date:** 2026-06-04
**Status:** Approved (design); implementation pending
**Module:** `auth/` (authentication layer; depends on `shared/tenant`, `core/`)

## Purpose

`auth/` establishes *who is making a request*. This spec covers the
**authentication slice only**: integrating the managed provider **Auth0**,
validating the JWT it issues, materialising a local `User` record, and exposing
a `get_current_user` dependency the rest of the app can build on. A `GET /me`
endpoint demonstrates the flow end-to-end.

The system has two human roles — `platform_admin` (our team) and `tenant` (the
customer) — both logging in through Auth0 (Google is configured as a social
connection *in Auth0*, not in our code).

### Supersedes prior plan

The original architecture notes (`CLAUDE.md`) described `auth/` as "Google
OAuth, sessions". This design **replaces custom OAuth with Auth0** (a managed
provider) per the decision to use Auth0/Clerk for authentication and future
RBAC. Consequently the scaffolded `auth/google_oauth.py` and `auth/session.py`
are obsolete and removed (Auth0 owns the Google login flow; a stateless
bearer-token model means there is no server-side session). `CLAUDE.md` is
updated when this work lands.

### Scope for this iteration — authentication only

**In scope:**

- Auth0 JWT validation (signature via JWKS, plus issuer/audience/expiry).
- A `Principal` built from token claims (subject, email, tenant_id, role).
- A local `User` table, find-or-created by Auth0 `sub`, linked to a `Tenant`.
- A `get_current_user` FastAPI dependency.
- A `GET /me` endpoint returning the current user.

**Explicitly deferred (later specs):**

- **Authorization** — role/tenant gates (`require_role`), tenant-scoping checks,
  and protecting the `admin`/`tenant` routers. Nothing to guard yet (those
  routers are empty), and RBAC will ultimately be Auth0's responsibility.
  `auth/permissions.py` stays empty for now.
- **KYC / GST verification** — this verifies a *business*, so the GST number and
  KYC status belong on the `Tenant` (`shared/tenant`) or in
  `modules/tenant_onboarding`, **not** on `User`. The `User ↔ tenant_id` link
  built here is its prerequisite, but KYC itself is a separate feature.

## Why a local User table (not stateless claims)

A stateless principal (claims only, no row) was the smaller option, but the
**KYC requirement is the real consumer** that justifies persistence: once a
logged-in user must complete KYC, we need a durable place to anchor
user/tenant-scoped application state. So we keep a `User` row keyed by the Auth0
`sub`, carrying `email`, `tenant_id`, and `role`, created find-or-create on the
first authenticated request.

## User ↔ Tenant relationship

- **For now, a `Tenant` has exactly one user** — the tenant *is* that single
  login user. (A tenant has many *leads*, but that is the future lead modules,
  not the `User` table.)
- A `tenant`-role user belongs to exactly one `Tenant` (`tenant_id` set, FK to
  `tenants.id`).
- A `platform_admin` user has **`tenant_id = NULL`** (our team, not tied to a
  customer).
- The "one user per tenant" rule is enforced by a **UNIQUE constraint on
  `tenant_id`**. Postgres treats multiple `NULL`s as distinct, so this still
  permits many `platform_admin` rows while allowing at most one user per
  tenant. When multiple users per tenant are needed later, dropping this single
  constraint is the only change required.
- `tenant_id` and `role` arrive as **custom namespaced claims** in the Auth0
  token (see below); our `Tenant` table remains the source of truth for tenant
  identity.

## File layout

Maps onto the existing `auth/` scaffold; obsolete files removed, one added.

```
auth/
  __init__.py        # empty (consistent with shared/tenant)
  schemas.py         # public: Role enum, Principal, UserRead
  models.py          # internal: User ORM
  token.py           # NEW: Auth0 JWKS fetch+cache + JWT verification -> claims
  service.py         # public: get_or_create_user(session, principal)
  dependencies.py    # get_current_user FastAPI dependency
  permissions.py     # left EMPTY (authorization, future spec)
  # google_oauth.py  -> REMOVED (Auth0 owns Google login)
  # session.py       -> REMOVED (stateless bearer-token model, no server session)
api/
  me.py              # NEW: router with GET /me, included in main.py
core/
  config.py          # add Auth0 settings
  exceptions.py      # add AuthenticationError (401)
migrations/versions/ # new migration creating the users table
```

Public surface other code imports: `auth/schemas.py`, `auth/service.py`, and
`auth/dependencies.py`. `models.py` and `token.py` are internal.

## Config (`core/config.py`)

Add to `Settings`:

```python
auth0_domain: str = ""                                # e.g. "leadengine.us.auth0.com"
auth0_audience: str = ""                              # the API identifier in Auth0
auth0_algorithms: list[str] = ["RS256"]
auth_claim_namespace: str = "https://leadengine/"     # prefix for custom claims
```

Derived (computed in `token.py`, not stored): issuer = `https://{auth0_domain}/`;
JWKS URL = `https://{auth0_domain}/.well-known/jwks.json`.

## Token validation (`auth/token.py`)

Uses `python-jose[cryptography]` (already a dependency) and `httpx`:

1. **JWKS cache** — fetch Auth0's JWKS via `httpx`, cached in an in-process
   `cachetools` TTLCache (1-hour TTL). **Invalidation path:** if a presented
   token's `kid` is absent from the cached keyset, refetch the JWKS once before
   failing. This satisfies the project rule that every cache has both a TTL and
   an explicit invalidation path. The cache lives in `token.py`, *not*
   `core/cache.py` (which remains deferred).
2. **Verification** — verify the JWT's RS256 signature against the matching JWK,
   and validate `issuer`, `audience`, and `exp`.
3. **Result** — return the decoded claims dict on success. Any failure (missing
   header, malformed token, bad signature, expired, wrong `aud`/`iss`, unknown
   `kid` after refetch) raises `AuthenticationError`.

## Data model (`auth/schemas.py`, `auth/models.py`)

### `Role` (StrEnum, public)

`PLATFORM_ADMIN`, `TENANT`.

### `Principal` (pydantic, public) — validated identity, not persisted

| Field       | Type          | Source                                   |
|-------------|---------------|------------------------------------------|
| `subject`   | `str`         | standard `sub` claim                     |
| `email`     | `EmailStr`    | standard `email` claim                   |
| `tenant_id` | `UUID \| None`| custom claim `{namespace}tenant_id`      |
| `role`      | `Role`        | custom claim `{namespace}role`           |

A missing/invalid required claim raises `AuthenticationError`. A
`platform_admin` legitimately has `tenant_id = None`.

### `User` ORM (`auth/models.py`, table `users`) — internal

| Column        | Type            | Rules                                          |
|---------------|-----------------|------------------------------------------------|
| `id`          | UUID            | PK, generated `uuid4`                           |
| `auth0_sub`   | str             | **unique**, not null                            |
| `email`       | str             | not null                                        |
| `role`        | enum `user_role`| not null (`PLATFORM_ADMIN` / `TENANT`)          |
| `tenant_id`   | UUID \| null    | nullable FK → `tenants.id`, **UNIQUE** (one user per tenant for now; NULL for admins) |
| `created_at`  | tz datetime     | `server_default=func.now()`                     |
| `updated_at`  | tz datetime     | `server_default=func.now()`, `onupdate=func.now()` |

`UserRead` (pydantic, `from_attributes`) mirrors the row for the `/me` response.
Migration creates `users` (Alembic, sync via psycopg2, per the established
pattern), with the `auth0_sub` unique index and the `tenant_id` unique FK.

## Service & dependency

### `auth/service.py` (public)

```python
async def get_or_create_user(session: AsyncSession, principal: Principal) -> User
```

Look up a `User` by `auth0_sub`. If absent, create it from the principal's
fields. If present, refresh `email`, `role`, and `tenant_id` from the principal
(the Auth0 token is the source of truth for those, so changes in Auth0
propagate on next login) and return it. This is the only public path that
materialises a user.

### `auth/dependencies.py`

```python
async def get_current_user(...) -> User
```

1. Extract the `Bearer` token (`fastapi.security.HTTPBearer`).
2. `token.verify(...)` → claims.
3. Build a `Principal` from the claims.
4. `get_or_create_user(session, principal)` → `User`.
5. Return the `User`. Any validation failure surfaces as `AuthenticationError`.

This dependency is the seam every authenticated route depends on.

## API (`api/me.py`)

A small `APIRouter` with `GET /me` depending on `get_current_user`, returning
`UserRead`. Included from `main.py` (alongside `/health`). It demonstrates the
full validate → resolve → persist flow and is the integration-test target.

## Error handling (`core/exceptions.py`)

Add:

```python
class AuthenticationError(AppError):
    """Authentication failed (missing/invalid token or claims)."""

    status_code = 401
```

(`AuthorizationError` / 403 is deferred to the authorization spec.)

**Edge cases & their responses:**

- Missing/malformed `Authorization` header → 401.
- Bad signature / expired / wrong `aud` / wrong `iss` → 401.
- Unknown `kid` even after one JWKS refetch → 401.
- Missing required claim (`sub`, `email`, `role`) → 401.
- `tenant_id` claim referencing a non-existent `Tenant` → FK violation, surfaced
  as a clear auth error ("tenant not provisioned").

## Testing (TDD)

### Unit (no DB, no network)

- **Token validation** — generate an RSA keypair in-test, build a JWKS from the
  public key, mint tokens with `python-jose`; the JWKS fetch is
  monkeypatched / `respx`-mocked so nothing hits the network. Cases:
  valid → claims; expired → error; wrong audience → error; wrong issuer → error;
  bad signature (signed with a different key) → error; unknown `kid` triggers
  exactly one refetch then succeeds/fails accordingly.
- **Schemas** — `Role` membership is exactly `{PLATFORM_ADMIN, TENANT}`;
  `Principal` builds from a claims dict; missing required claim raises;
  `platform_admin` with `tenant_id = None` is valid.

### Integration (real Postgres)

- `get_or_create_user` creates a row on first call and returns the same row on
  the second (no duplicate).
- A `platform_admin` principal yields a row with `tenant_id IS NULL`; two
  distinct admins both persist (NULLs do not collide on the unique constraint).
- A `tenant` principal links to a `Tenant` created via `shared/tenant`; a second
  user for the same tenant violates the `tenant_id` unique constraint.
- Migration `upgrade` creates the `users` table with the FK to `tenants` and the
  unique constraints.

### API (`TestClient`, token verification patched to the in-test key)

- `GET /me` with a valid token → 200, returns the user JSON, and a `users` row
  is persisted.
- `GET /me` with no token → 401.
- `GET /me` with an invalid token → 401.

## Consequences & follow-ups

- Establishes the stateless-Auth0 authentication seam; the rest of the app gets
  the current user via `get_current_user` without knowing about Auth0.
- **Deferred:** authorization (`permissions.py`, `require_role`, tenant-scoping,
  protecting admin/tenant routers) — its own spec, when there are routes to
  guard.
- **Deferred:** KYC / GST verification — a `Tenant`/`tenant_onboarding` feature
  built on the `User ↔ tenant_id` link from here.
- **Deferred:** multiple users per tenant — relax by dropping the `tenant_id`
  unique constraint when that need arises.
- **Auth0 configuration** (an Action injecting the `{namespace}tenant_id` and
  `{namespace}role` claims, the Google social connection, the API audience) is
  an operational setup task documented alongside deployment, not application
  code.
- `core/cache.py` remains deferred; the JWKS cache is intentionally local to
  `auth/token.py`.

## Reference

- `shared/tenant` — the `Tenant` entity `User.tenant_id` references; precedent
  for ORM model + migration + integration-test structure.
- `core/exceptions.py` — `AppError` hierarchy that `AuthenticationError` joins.
- `docs/architecture.md` — the dependency rule (`api/` → `auth/` → `shared/` →
  `core/`).
