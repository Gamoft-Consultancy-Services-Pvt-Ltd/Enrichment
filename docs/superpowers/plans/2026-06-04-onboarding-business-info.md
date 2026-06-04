# Onboarding — collect tenant business info — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an authenticated (Auth0) user submit their business info, which creates a `Tenant` and links it to their `User` — the "log in → onboard" loop, demoable via `/docs`.

**Architecture:** A `POST /onboarding` endpoint (requires `get_current_user`) reuses `shared/tenant`'s `create_tenant`, then links the new tenant to the user via a small `auth.service.set_user_tenant`. Our DB owns the user→tenant link, so `get_or_create_user` is fixed to stop wiping `tenant_id` when a login token carries none.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 (async), pydantic v2, pytest + `respx`, real Postgres for integration tests.

**Spec:** `docs/superpowers/specs/2026-06-04-onboarding-business-info-design.md`

> **Environment note:** All tasks here have integration tests requiring a running Postgres (`DATABASE_URL`). Postgres is already up via `docker compose up -d postgres` and migrations are applied (no new migration in this slice).

---

## File Structure

- `auth/service.py` — **modify**: fix `get_or_create_user` (no `tenant_id` wipe); add `set_user_tenant`.
- `api/onboarding.py` — **create**: `POST /onboarding` router.
- `main.py` — **modify**: include the onboarding router.
- `tests/integration/test_auth_service.py` — **modify**: add no-wipe + `set_user_tenant` tests.
- `tests/integration/test_onboarding_endpoint.py` — **create**: endpoint tests.

Reuses (no change): `shared/tenant/service.create_tenant`, `shared/tenant/schemas.TenantCreate`/`TenantRead`, `auth/dependencies.get_current_user`, `core/exceptions.ConflictError`.

---

## Task 1: Stop `get_or_create_user` from wiping `tenant_id`

**Files:**
- Modify: `auth/service.py`
- Test: `tests/integration/test_auth_service.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_auth_service.py` (it already imports `get_or_create_user`, `Principal`, `Role`, `User`, `select`, `AsyncSession`, and the tenant helpers):

```python
async def test_existing_tenant_link_is_preserved_when_token_lacks_tenant(
    session: AsyncSession,
) -> None:
    # User first appears already linked to a tenant (e.g. claim carried it once).
    tenant = await create_tenant(
        session,
        TenantCreate(
            company_name="Acme",
            primary_contact_name="Ada",
            primary_contact_email="ada@acme.com",
            business_type=BusinessType.B2B,
        ),
    )
    linked = Principal(
        subject="auth0|keep", email="ada@acme.com", tenant_id=tenant.id, role=Role.TENANT
    )
    await get_or_create_user(session, linked)

    # Next login: same user, but the token carries NO tenant_id.
    tokenless = Principal(
        subject="auth0|keep", email="ada@acme.com", tenant_id=None, role=Role.TENANT
    )
    user = await get_or_create_user(session, tokenless)

    assert user.tenant_id == tenant.id  # preserved, not wiped
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_auth_service.py::test_existing_tenant_link_is_preserved_when_token_lacks_tenant -v`
Expected: FAIL — `assert None == <tenant.id>` (the current code nulls it).

- [ ] **Step 3: Write minimal implementation**

In `auth/service.py`, change the `else` branch of `get_or_create_user` so `tenant_id` is only overwritten when the principal carries one:

```python
    else:
        user.email = principal.email
        user.role = principal.role
        if principal.tenant_id is not None:
            user.tenant_id = principal.tenant_id
```

(The `if user is None:` create branch is unchanged — it still sets `tenant_id=principal.tenant_id`, which may be `None` on first login.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_auth_service.py -v`
Expected: PASS (all prior service tests + the new one).

- [ ] **Step 5: Commit**

```bash
git add auth/service.py tests/integration/test_auth_service.py
git commit -m "fix: preserve DB-owned tenant_id when login token carries none"
```

---

## Task 2: `set_user_tenant` link helper

**Files:**
- Modify: `auth/service.py`
- Test: `tests/integration/test_auth_service.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_auth_service.py`:

```python
async def test_set_user_tenant_links_and_persists(session: AsyncSession) -> None:
    from auth.service import set_user_tenant

    user = await get_or_create_user(session, _admin_principal("auth0|link"))
    assert user.tenant_id is None

    tenant = await create_tenant(
        session,
        TenantCreate(
            company_name="Beta",
            primary_contact_name="Bo",
            primary_contact_email="bo@beta.com",
            business_type=BusinessType.B2C,
        ),
    )
    updated = await set_user_tenant(session, user, tenant.id)
    assert updated.tenant_id == tenant.id

    # Confirm it persisted by re-reading.
    fresh = (
        await session.execute(select(User).where(User.auth0_sub == "auth0|link"))
    ).scalar_one()
    assert fresh.tenant_id == tenant.id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_auth_service.py::test_set_user_tenant_links_and_persists -v`
Expected: FAIL — `ImportError: cannot import name 'set_user_tenant' from 'auth.service'`.

- [ ] **Step 3: Write minimal implementation**

In `auth/service.py`, add the import of `UUID` if not present (top of file: `from uuid import UUID`), then add:

```python
async def set_user_tenant(session: AsyncSession, user: User, tenant_id: UUID) -> User:
    """Link a user to a tenant and persist it."""
    user.tenant_id = tenant_id
    await session.commit()
    await session.refresh(user)
    return user
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_auth_service.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add auth/service.py tests/integration/test_auth_service.py
git commit -m "feat: add set_user_tenant link helper"
```

---

## Task 3: `POST /onboarding` endpoint

**Files:**
- Create: `api/onboarding.py`
- Modify: `main.py`
- Test: `tests/integration/test_onboarding_endpoint.py`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_onboarding_endpoint.py`:

```python
"""Integration tests for POST /onboarding — TestClient with token verification patched."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import auth.token as token_module
from auth.models import User
from main import app

NS = "https://leadengine/"

_BUSINESS = {
    "company_name": "Acme",
    "primary_contact_name": "Ada",
    "primary_contact_email": "ada@acme.com",
    "business_type": "B2B",
    "timezone": "UTC",
    "language_preference": "en",
}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """TestClient whose token verification is stubbed by the Bearer string.

    'tenant-token' is a logged-in TENANT user who has NOT onboarded yet
    (no tenant_id claim).
    """

    def fake_verify(token: str) -> dict[str, Any]:
        if token == "tenant-token":
            return {"sub": "auth0|newtenant", "email": "ada@acme.com", f"{NS}role": "TENANT"}
        from core.exceptions import AuthenticationError

        raise AuthenticationError("bad token")

    monkeypatch.setattr(token_module, "verify_token", fake_verify)
    return TestClient(app)


def _auth() -> dict[str, str]:
    return {"Authorization": "Bearer tenant-token"}


async def test_onboarding_creates_tenant_and_links_user(
    client: TestClient, session: AsyncSession
) -> None:
    # First touch /me so the user row exists with no tenant.
    me = client.get("/me", headers=_auth())
    assert me.status_code == 200
    assert me.json()["tenant_id"] is None

    resp = client.post("/onboarding", headers=_auth(), json=_BUSINESS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["company_name"] == "Acme"
    assert body["business_type"] == "B2B"
    assert body["status"] == "CREATED"

    # The user is now linked to the created tenant.
    user = (
        await session.execute(select(User).where(User.auth0_sub == "auth0|newtenant"))
    ).scalar_one()
    assert str(user.tenant_id) == body["id"]


def test_me_reflects_tenant_after_onboarding(client: TestClient) -> None:
    client.post("/onboarding", headers=_auth(), json=_BUSINESS)
    me = client.get("/me", headers=_auth())
    assert me.status_code == 200
    assert me.json()["tenant_id"] is not None


def test_second_onboarding_is_conflict(client: TestClient) -> None:
    first = client.post("/onboarding", headers=_auth(), json=_BUSINESS)
    assert first.status_code == 200
    second = client.post("/onboarding", headers=_auth(), json=_BUSINESS)
    assert second.status_code == 409


def test_onboarding_without_token_is_401(client: TestClient) -> None:
    resp = client.post("/onboarding", json=_BUSINESS)
    assert resp.status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_onboarding_endpoint.py -v`
Expected: FAIL — `POST /onboarding` returns 404 (route doesn't exist yet) / 405.

- [ ] **Step 3: Create the router**

Create `api/onboarding.py`:

```python
"""The /onboarding endpoint — the current user submits business info to create their tenant."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import get_current_user
from auth.models import User
from auth.service import set_user_tenant
from core.db import get_session
from core.exceptions import ConflictError
from shared.tenant.schemas import TenantCreate, TenantRead
from shared.tenant.service import create_tenant

router = APIRouter()


@router.post("/onboarding", response_model=TenantRead)
async def onboard(
    data: TenantCreate,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TenantRead:
    """Create the current user's tenant from their business info, then link it."""
    if user.tenant_id is not None:
        raise ConflictError("User is already onboarded to a tenant")
    tenant = await create_tenant(session, data)
    await set_user_tenant(session, user, tenant.id)
    return TenantRead.model_validate(tenant)
```

- [ ] **Step 4: Wire it into `main.py`**

Add the import and include the router (alongside the existing `me_router`):

```python
from api.me import router as me_router
from api.onboarding import router as onboarding_router
```

```python
app.include_router(me_router)
app.include_router(onboarding_router)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_onboarding_endpoint.py -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add api/onboarding.py main.py tests/integration/test_onboarding_endpoint.py
git commit -m "feat: add POST /onboarding to create and link a tenant"
```

---

## Task 4: Full-suite gate

**Files:** none (verification only)

- [ ] **Step 1: Run the local CI gate**

Run: `make ci`
Expected: PASS — `ruff check .` clean, `mypy .` (strict) clean, full pytest suite green (prior tests + the new service + onboarding tests). Postgres must be running.

- [ ] **Step 2: If anything fails, fix it**

Likely issues and fixes:
- `ruff` import ordering on the new file — run `make format`, then re-run `make ci`.
- `mypy` on `auth/service.py` — ensure `from uuid import UUID` is imported for the `set_user_tenant` signature.
- If `TenantRead.model_validate(tenant)` trips mypy on return type, it already matches the declared `-> TenantRead`; no change expected.

- [ ] **Step 3: Commit any fixes**

```bash
git add -A
git commit -m "chore: satisfy lint/typecheck for onboarding slice"
```

(Skip if Step 1 passed clean.)

---

## Manual demo (after the gate, optional)

With Auth0 configured and a `.env` (`AUTH0_DOMAIN`, `AUTH0_AUDIENCE`), and a token for a `TENANT` user that has no tenant yet:

1. `uv run uvicorn main:app --reload --port 8000`
2. Open `http://localhost:8000/docs`, click **Authorize**, paste the token.
3. `GET /me` → `tenant_id` is `null`.
4. `POST /onboarding` with the business fields → 200, returns the tenant.
5. `GET /me` → `tenant_id` now populated.

---

## Self-Review Notes

- **Spec coverage:** login `tenant_id`-wipe fix (T1) · `set_user_tenant` (T2) · `POST /onboarding` with `TenantCreate`→`TenantRead`, 409-if-already-onboarded, reuse `create_tenant`, status stays `CREATED`, wired in `main.py` (T3) · `/me` reflects tenant (T3 test) · 401/409/422 error handling (T3 tests cover 401 & 409; 422 is automatic pydantic) · DB-owns-link decision realized (T1+T3) · full gate (T4). No frontend / no agent chain / no Auth0 write-back — all explicitly deferred, no task. ✅
- **Placeholders:** none — every code step shows complete code.
- **Type consistency:** `get_or_create_user(session, principal) -> User`, `set_user_tenant(session, user, tenant_id: UUID) -> User`, `create_tenant(session, data: TenantCreate) -> Tenant`, `TenantRead.model_validate(...)`, `onboard(...) -> TenantRead` — consistent across tasks and with the existing codebase. `verify_token` stub pattern matches `tests/integration/test_me_endpoint.py`.
