# Auth0 setup guide

This guide covers the **operational configuration** needed to make authentication
work end to end. The application code (validating Auth0 JWTs, resolving a local
`User`, the `GET /me` endpoint) is already built and tested — but it does nothing
until an Auth0 account exists and the app is pointed at it. See the design in
`docs/superpowers/specs/2026-06-04-auth-authentication-design.md`.

> **Terminology warning:** Auth0 calls your *account* a "tenant". That is **not**
> the same as a *tenant* in this product (a customer business / `tenants` table).
> Wherever this guide says "Auth0 tenant" it means your Auth0 account/domain.

## How our backend uses Auth0 (the contract)

Our API is **stateless**: the frontend logs the user in with Auth0 and sends the
resulting JWT as `Authorization: Bearer <token>` on every request. For each
request the backend (`auth/token.py`) verifies the token's **RS256 signature**
against Auth0's published public keys (JWKS), and checks the **issuer**,
**audience**, and **expiry**. It then reads these claims (`auth/schemas.py`,
`Principal.from_claims`):

| Claim                              | Maps to        | Required | Notes                                   |
|------------------------------------|----------------|----------|-----------------------------------------|
| `sub`                              | `subject`      | yes      | Auth0's stable user id (always present) |
| `email`                            | `email`        | yes      | **Not in an access token by default** — see "Email claim gotcha" |
| `https://leadengine/role`          | `role`         | yes      | `PLATFORM_ADMIN` or `TENANT`            |
| `https://leadengine/tenant_id`     | `tenant_id`    | no       | a `Tenant.id` UUID; omit/empty for admins |

The namespace prefix `https://leadengine/` is the `AUTH_CLAIM_NAMESPACE` setting.

## Step-by-step Auth0 configuration

### 1. Create an Auth0 account/tenant
Sign up at auth0.com. You'll get a domain such as `dev-abc123.us.auth0.com`.
This domain is the `AUTH0_DOMAIN` setting and determines:
- issuer: `https://dev-abc123.us.auth0.com/`
- JWKS URL: `https://dev-abc123.us.auth0.com/.well-known/jwks.json`

### 2. Create an API (this defines the audience)
Dashboard → **Applications → APIs → Create API**.
- **Name:** Lead Intelligence Engine API
- **Identifier:** a URL-like string, e.g. `https://api.leadengine` — this is the
  `AUTH0_AUDIENCE` setting. (It never has to resolve; it's just an identifier.)
- **Signing algorithm:** RS256 (the default; our verifier only accepts RS256).

### 3. Create the frontend Application
Dashboard → **Applications → Applications → Create Application**.
- **Type:** Single Page Application (the dashboard frontend).
- Configure **Allowed Callback URLs / Logout URLs / Web Origins** for your
  frontend (e.g. `http://localhost:5173` in dev).
- The frontend uses this app's **Client ID** + your domain + the API audience
  (step 2) to log users in and obtain an access token.

### 4. Enable Google login
Dashboard → **Authentication → Social → Create Connection → Google**.
Provide Google OAuth credentials (or use Auth0's dev keys for testing) and enable
the connection for the Application from step 3. This is where "Login with Google"
now lives — *not* in our code.

### 5. Add a Post-Login Action to inject our custom claims
Dashboard → **Actions → Triggers → post-login → Add Action → Build from scratch**.
This copies the user's role/tenant (and email — see the gotcha) into the access
token so our backend can read them:

```js
exports.onExecutePostLogin = async (event, api) => {
  const namespace = "https://leadengine/";
  const meta = event.user.app_metadata || {};

  if (meta.role) {
    api.accessToken.setCustomClaim(`${namespace}role`, meta.role);
  }
  if (meta.tenant_id) {
    api.accessToken.setCustomClaim(`${namespace}tenant_id`, meta.tenant_id);
  }
  // See "Email claim gotcha" below for why email is added explicitly.
  if (event.user.email) {
    api.accessToken.setCustomClaim("email", event.user.email);
  }
};
```

Then drag the Action into the post-login flow and **Apply**.

### 6. Assign role and tenant_id to users
For each user, set `app_metadata` (Dashboard → **User Management → Users →
<user> → Metadata → app_metadata**, or via the Management API):

```json
{ "role": "TENANT", "tenant_id": "<a real Tenant.id UUID from our DB>" }
```

For our own team:

```json
{ "role": "PLATFORM_ADMIN" }
```

A `TENANT` user's `tenant_id` **must** match an existing row in our `tenants`
table (the `users.tenant_id` foreign key enforces this), so the tenant has to be
onboarded in our system first.

## Application configuration (`.env`)

There is no `.env` file yet — create one (the repo only ships `.env.example`).
The settings our code reads (`core/config.py`) are:

```dotenv
# Auth0
AUTH0_DOMAIN=dev-abc123.us.auth0.com
AUTH0_AUDIENCE=https://api.leadengine
# AUTH_CLAIM_NAMESPACE defaults to https://leadengine/ — only set to override
# AUTH0_ALGORITHMS defaults to ["RS256"]
```

> **Cleanup note:** `.env.example` still lists `GOOGLE_OAUTH_*` and
> `SESSION_SECRET_KEY` from the abandoned custom-OAuth plan. Those are obsolete
> (we deleted `auth/google_oauth.py` and `auth/session.py`) and should be
> replaced with the `AUTH0_*` keys above.

## Verifying it works

1. Obtain a **user** access token (log in through the frontend, or use Auth0's
   hosted login). Note: a **client-credentials** ("Test" tab) token is a *machine*
   token with no user — it won't carry `email`/`role`, so `/me` will reject it.
2. Call the API:
   ```bash
   curl -H "Authorization: Bearer <token>" http://localhost:8000/me
   ```
   Expect `200` with the user JSON; a missing/invalid token returns `401`.
3. The first successful call creates a `users` row (find-or-create); later calls
   refresh `email`/`role`/`tenant_id` from the token.

## Email claim gotcha (possible code follow-up)

Auth0 **access tokens** (issued for an API audience) do **not** include `email`
by default — `email` normally lives on the *ID token* / userinfo. Our backend
currently reads a top-level `email` claim, so the Post-Login Action in step 5
adds it explicitly. If your Auth0 tenant restricts non-namespaced custom claims,
add it as `https://leadengine/email` instead and we make a one-line change to
`Principal.from_claims` to read the namespaced key. Decide this when wiring the
real frontend.

## What is intentionally NOT covered yet

- **Authorization / RBAC enforcement** (`require_role`, tenant-scoping, protecting
  the admin/tenant routers) — a later spec. Auth0 will own RBAC; our
  `auth/permissions.py` is deliberately still empty.
- **KYC / GST verification** — a `Tenant` / `tenant_onboarding` feature built on
  the `User ↔ tenant_id` link.
- **The frontend application** itself.
