# auth — Auth0 Authentication Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate Auth0 so the backend validates the provider-issued JWT per request, materialises a local `User` (find-or-create by Auth0 `sub`, one user per tenant), and exposes `get_current_user` + a `GET /me` endpoint.

**Architecture:** Stateless bearer-token auth. `auth/token.py` verifies the JWT against Auth0's cached JWKS (issuer/audience/expiry). `auth/schemas.py` maps verified claims to a `Principal`. `auth/service.py` find-or-creates a `User` row. `auth/dependencies.py` wires those into a `get_current_user` FastAPI dependency, surfaced by `api/me.py`. Authorization gates and KYC are out of scope.

**Tech Stack:** FastAPI, `python-jose[cryptography]` (RS256 JWT), `httpx` + `cachetools` (JWKS cache), SQLAlchemy 2.0 + Alembic, pydantic v2, pytest + `respx`.

**Spec:** `docs/superpowers/specs/2026-06-04-auth-authentication-design.md`

> **Environment note:** Tasks 7–10 include integration/API tests and a migration that require a running Postgres (`DATABASE_URL`). Unit-test tasks (1–6, 9's handler test) need no DB. Run `make migrate` against the test DB before the integration tests.

---

## File Structure

- `core/exceptions.py` — **modify**: add `AuthenticationError` (401).
- `core/config.py` — **modify**: add Auth0 settings.
- `auth/schemas.py` — **create content**: `Role`, `Principal` (+`from_claims`), `UserRead`.
- `auth/token.py` — **create**: JWKS cache + `verify_token`.
- `auth/models.py` — **create content**: `User` ORM.
- `auth/service.py` — **create content**: `get_or_create_user`.
- `auth/dependencies.py` — **create content**: `get_current_user`.
- `auth/google_oauth.py`, `auth/session.py` — **delete** (obsolete).
- `api/middleware.py` — **create content**: `app_error_handler`.
- `api/me.py` — **create**: `GET /me` router.
- `main.py` — **modify**: register handler + include `/me` router.
- `migrations/env.py` — **modify**: import `auth.models`.
- `migrations/versions/<new>.py` — **create**: `users` table.
- `tests/helpers/auth.py` — **create**: JWT/JWKS test helpers.
- Tests under `tests/unit/` and `tests/integration/`.

Precedent to follow: `shared/tenant/models.py` (ORM style), `shared/tenant/schemas.py` (StrEnum + pydantic), `tests/unit/test_tenant_model.py`, `tests/unit/test_tenant_schemas.py`, `tests/integration/test_tenant_service.py`, `tests/integration/conftest.py` (`session` fixture), `migrations/versions/7c984ef0d9e8_*.py`.

---

## Task 1: Remove obsolete scaffold files

**Files:**
- Delete: `auth/google_oauth.py`, `auth/session.py`

- [ ] **Step 1: Confirm nothing imports them**

Run: `uv run python -c "import auth.google_oauth, auth.session"` (they're empty, so this only proves they exist). Then search:
Run (Grep tool): pattern `google_oauth|auth\.session|from auth import session` across the repo.
Expected: no references outside the files themselves.

- [ ] **Step 2: Delete the files**

```bash
git rm auth/google_oauth.py auth/session.py
```

- [ ] **Step 3: Verify the app still imports**

Run: `uv run python -c "import main; print('ok')"`
Expected: prints `ok` (no import errors).

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore: remove obsolete custom-OAuth scaffold (auth uses Auth0)"
```

---

## Task 2: `AuthenticationError` exception

**Files:**
- Modify: `core/exceptions.py`
- Test: `tests/unit/test_exceptions.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_exceptions.py`:

```python
def test_authentication_error_maps_to_401() -> None:
    from core.exceptions import AppError, AuthenticationError

    exc = AuthenticationError("nope")
    assert isinstance(exc, AppError)
    assert exc.status_code == 401
    assert exc.message == "nope"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_exceptions.py::test_authentication_error_maps_to_401 -v`
Expected: FAIL — `ImportError: cannot import name 'AuthenticationError'`.

- [ ] **Step 3: Write minimal implementation**

In `core/exceptions.py`, add after `ConflictError`:

```python
class AuthenticationError(AppError):
    """Authentication failed (missing/invalid token or claims)."""

    status_code = 401
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_exceptions.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/exceptions.py tests/unit/test_exceptions.py
git commit -m "feat: add AuthenticationError (401) to exception hierarchy"
```

---

## Task 3: Auth0 settings

**Files:**
- Modify: `core/config.py`
- Test: `tests/unit/test_config.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_config.py` (uses `tests.helpers.build_settings`):

```python
def test_auth0_settings_have_sensible_defaults() -> None:
    from tests.helpers import build_settings

    settings = build_settings()
    assert settings.auth0_domain == ""
    assert settings.auth0_audience == ""
    assert settings.auth0_algorithms == ["RS256"]
    assert settings.auth_claim_namespace == "https://leadengine/"


def test_auth0_settings_are_overridable() -> None:
    from tests.helpers import build_settings

    settings = build_settings(auth0_domain="acme.us.auth0.com", auth0_audience="api://leadengine")
    assert settings.auth0_domain == "acme.us.auth0.com"
    assert settings.auth0_audience == "api://leadengine"
```

If `tests/unit/test_config.py` does not already `from tests.helpers import build_settings`, the inline imports above keep the test self-contained — leave them as written.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_config.py -k auth0 -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'auth0_domain'`.

- [ ] **Step 3: Write minimal implementation**

In `core/config.py`, add these fields to the `Settings` class (after `database_url`):

```python
    auth0_domain: str = ""
    auth0_audience: str = ""
    auth0_algorithms: list[str] = ["RS256"]
    auth_claim_namespace: str = "https://leadengine/"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_config.py -k auth0 -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add core/config.py tests/unit/test_config.py
git commit -m "feat: add Auth0 settings to core config"
```

---

## Task 4: `Role`, `Principal`, `UserRead` schemas

**Files:**
- Modify: `auth/schemas.py` (currently empty)
- Test: `tests/unit/test_auth_schemas.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_auth_schemas.py`:

```python
"""Unit tests for auth.schemas — pure validation, no DB."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from core.exceptions import AuthenticationError
from auth.schemas import Principal, Role, UserRead

NS = "https://leadengine/"


def test_role_membership_is_exact() -> None:
    assert {m.value for m in Role} == {"PLATFORM_ADMIN", "TENANT"}


def test_principal_from_claims_tenant_user() -> None:
    tenant_id = uuid4()
    claims = {
        "sub": "auth0|abc",
        "email": "user@acme.com",
        f"{NS}role": "TENANT",
        f"{NS}tenant_id": str(tenant_id),
    }
    p = Principal.from_claims(claims, NS)
    assert p.subject == "auth0|abc"
    assert p.email == "user@acme.com"
    assert p.role is Role.TENANT
    assert p.tenant_id == tenant_id


def test_principal_from_claims_admin_has_no_tenant() -> None:
    claims = {"sub": "auth0|admin", "email": "ops@us.com", f"{NS}role": "PLATFORM_ADMIN"}
    p = Principal.from_claims(claims, NS)
    assert p.role is Role.PLATFORM_ADMIN
    assert p.tenant_id is None


def test_principal_from_claims_missing_email_raises_auth_error() -> None:
    claims = {"sub": "auth0|abc", f"{NS}role": "TENANT"}
    with pytest.raises(AuthenticationError):
        Principal.from_claims(claims, NS)


def test_principal_from_claims_invalid_role_raises_auth_error() -> None:
    claims = {"sub": "auth0|abc", "email": "u@a.com", f"{NS}role": "WIZARD"}
    with pytest.raises(AuthenticationError):
        Principal.from_claims(claims, NS)


def test_user_read_builds_from_orm_like_object() -> None:
    obj = SimpleNamespace(
        id=uuid4(),
        auth0_sub="auth0|abc",
        email="user@acme.com",
        role=Role.TENANT,
        tenant_id=uuid4(),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    read = UserRead.model_validate(obj)
    assert read.auth0_sub == "auth0|abc"
    assert read.role is Role.TENANT
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_auth_schemas.py -v`
Expected: FAIL — `ImportError: cannot import name 'Principal' from 'auth.schemas'`.

- [ ] **Step 3: Write minimal implementation**

Write `auth/schemas.py`:

```python
"""Public auth schemas: the Role enum, the request Principal, and UserRead."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, ValidationError

from core.exceptions import AuthenticationError


class Role(StrEnum):
    """The two human roles, sourced from the Auth0 token's role claim."""

    PLATFORM_ADMIN = "PLATFORM_ADMIN"
    TENANT = "TENANT"


class Principal(BaseModel):
    """The authenticated identity, built from verified token claims; not persisted."""

    model_config = ConfigDict(frozen=True)

    subject: str
    email: EmailStr
    tenant_id: UUID | None
    role: Role

    @classmethod
    def from_claims(cls, claims: dict[str, Any], namespace: str) -> Principal:
        """Map a verified claims dict to a Principal, or raise AuthenticationError."""
        try:
            raw_tenant = claims.get(f"{namespace}tenant_id")
            return cls(
                subject=claims["sub"],
                email=claims["email"],
                role=Role(claims[f"{namespace}role"]),
                tenant_id=UUID(raw_tenant) if raw_tenant else None,
            )
        except (KeyError, ValueError, ValidationError) as exc:
            raise AuthenticationError("Invalid or missing token claims") from exc


class UserRead(BaseModel):
    """The user record returned to callers (e.g. GET /me); built from the ORM object."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    auth0_sub: str
    email: EmailStr
    role: Role
    tenant_id: UUID | None
    created_at: datetime
    updated_at: datetime
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_auth_schemas.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add auth/schemas.py tests/unit/test_auth_schemas.py
git commit -m "feat: add Role, Principal, UserRead auth schemas"
```

---

## Task 5: JWT/JWKS test helpers + `auth/token.py`

**Files:**
- Create: `tests/helpers/auth.py`
- Create: `auth/token.py`
- Test: `tests/unit/test_auth_token.py`

- [ ] **Step 1: Write the JWT/JWKS test helper**

Create `tests/helpers/auth.py`:

```python
"""Helpers for minting RS256 JWTs and JWKS in auth tests (no network)."""

from __future__ import annotations

import time
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwk, jwt
from jose.constants import ALGORITHMS


def make_keypair(kid: str = "test-key") -> tuple[str, dict[str, Any]]:
    """Return (private_pem, public_jwk) where the JWK carries the given kid."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    jwk_dict = jwk.construct(public_pem, ALGORITHMS.RS256).to_dict()
    jwk_dict = {k: (v.decode() if isinstance(v, bytes) else v) for k, v in jwk_dict.items()}
    jwk_dict.update({"kid": kid, "use": "sig", "alg": "RS256"})
    return private_pem, jwk_dict


def make_token(
    private_pem: str,
    *,
    kid: str,
    audience: str,
    issuer: str,
    claims: dict[str, Any],
    expires_in: int = 3600,
) -> str:
    """Sign a JWT with the given private key, kid, aud/iss, and extra claims."""
    now = int(time.time())
    payload = {**claims, "iss": issuer, "aud": audience, "iat": now, "exp": now + expires_in}
    return jwt.encode(payload, private_pem, algorithm="RS256", headers={"kid": kid})
```

- [ ] **Step 2: Write the failing test**

Create `tests/unit/test_auth_token.py`:

```python
"""Unit tests for auth.token — JWT verification against a mocked JWKS, no network."""

from collections.abc import Iterator

import httpx
import pytest
import respx

from core.exceptions import AuthenticationError
from tests.helpers import build_settings
from tests.helpers.auth import make_keypair, make_token

DOMAIN = "test.auth0.com"
AUDIENCE = "api://leadengine"
ISSUER = f"https://{DOMAIN}/"
JWKS_URL = f"https://{DOMAIN}/.well-known/jwks.json"


@pytest.fixture(autouse=True)
def _settings_and_clean_cache(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    import auth.token as token_module

    monkeypatch.setattr(
        token_module,
        "get_settings",
        lambda: build_settings(auth0_domain=DOMAIN, auth0_audience=AUDIENCE),
    )
    token_module._JWKS_CACHE.clear()
    yield
    token_module._JWKS_CACHE.clear()


def _claims() -> dict[str, str]:
    return {"sub": "auth0|abc", "email": "user@acme.com"}


@respx.mock
def test_valid_token_returns_claims() -> None:
    from auth.token import verify_token

    private_pem, public_jwk = make_keypair(kid="test-key")
    respx.get(JWKS_URL).mock(return_value=httpx.Response(200, json={"keys": [public_jwk]}))
    token = make_token(private_pem, kid="test-key", audience=AUDIENCE, issuer=ISSUER, claims=_claims())

    claims = verify_token(token)
    assert claims["sub"] == "auth0|abc"
    assert claims["email"] == "user@acme.com"


@respx.mock
def test_jwks_is_cached_after_first_fetch() -> None:
    from auth.token import verify_token

    private_pem, public_jwk = make_keypair(kid="test-key")
    route = respx.get(JWKS_URL).mock(return_value=httpx.Response(200, json={"keys": [public_jwk]}))
    token = make_token(private_pem, kid="test-key", audience=AUDIENCE, issuer=ISSUER, claims=_claims())

    verify_token(token)
    verify_token(token)
    assert route.call_count == 1


@respx.mock
def test_expired_token_raises() -> None:
    from auth.token import verify_token

    private_pem, public_jwk = make_keypair(kid="test-key")
    respx.get(JWKS_URL).mock(return_value=httpx.Response(200, json={"keys": [public_jwk]}))
    token = make_token(
        private_pem, kid="test-key", audience=AUDIENCE, issuer=ISSUER, claims=_claims(), expires_in=-10
    )

    with pytest.raises(AuthenticationError):
        verify_token(token)


@respx.mock
def test_wrong_audience_raises() -> None:
    from auth.token import verify_token

    private_pem, public_jwk = make_keypair(kid="test-key")
    respx.get(JWKS_URL).mock(return_value=httpx.Response(200, json={"keys": [public_jwk]}))
    token = make_token(
        private_pem, kid="test-key", audience="api://wrong", issuer=ISSUER, claims=_claims()
    )

    with pytest.raises(AuthenticationError):
        verify_token(token)


@respx.mock
def test_wrong_issuer_raises() -> None:
    from auth.token import verify_token

    private_pem, public_jwk = make_keypair(kid="test-key")
    respx.get(JWKS_URL).mock(return_value=httpx.Response(200, json={"keys": [public_jwk]}))
    token = make_token(
        private_pem, kid="test-key", audience=AUDIENCE, issuer="https://evil.example/", claims=_claims()
    )

    with pytest.raises(AuthenticationError):
        verify_token(token)


@respx.mock
def test_bad_signature_raises() -> None:
    from auth.token import verify_token

    signing_pem, _ = make_keypair(kid="test-key")
    _, other_jwk = make_keypair(kid="test-key")  # different key, same kid
    respx.get(JWKS_URL).mock(return_value=httpx.Response(200, json={"keys": [other_jwk]}))
    token = make_token(signing_pem, kid="test-key", audience=AUDIENCE, issuer=ISSUER, claims=_claims())

    with pytest.raises(AuthenticationError):
        verify_token(token)


@respx.mock
def test_unknown_kid_triggers_one_refetch_then_fails() -> None:
    from auth.token import verify_token

    private_pem, public_jwk = make_keypair(kid="known-key")
    route = respx.get(JWKS_URL).mock(return_value=httpx.Response(200, json={"keys": [public_jwk]}))
    token = make_token(private_pem, kid="unknown-key", audience=AUDIENCE, issuer=ISSUER, claims=_claims())

    with pytest.raises(AuthenticationError):
        verify_token(token)
    assert route.call_count == 2  # initial + one forced refresh
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_auth_token.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'auth.token'`.

- [ ] **Step 4: Write minimal implementation**

Create `auth/token.py`:

```python
"""Auth0 JWT verification: fetch & cache the JWKS, then verify the bearer token."""

from __future__ import annotations

from typing import Any

import httpx
from cachetools import TTLCache
from jose import jwt
from jose.exceptions import JWTError

from core.config import Settings, get_settings
from core.exceptions import AuthenticationError

_JWKS_TTL_SECONDS = 3600
_JWKS_CACHE: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=1, ttl=_JWKS_TTL_SECONDS)
_CACHE_KEY = "jwks"


def _issuer(settings: Settings) -> str:
    return f"https://{settings.auth0_domain}/"


def _jwks_url(settings: Settings) -> str:
    return f"https://{settings.auth0_domain}/.well-known/jwks.json"


def _get_jwks(*, force_refresh: bool = False) -> dict[str, Any]:
    """Return Auth0's JWKS, served from a 1-hour TTL cache; refetch when forced."""
    if not force_refresh and _CACHE_KEY in _JWKS_CACHE:
        return _JWKS_CACHE[_CACHE_KEY]
    try:
        response = httpx.get(_jwks_url(get_settings()), timeout=5.0)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise AuthenticationError("Unable to fetch signing keys") from exc
    jwks: dict[str, Any] = response.json()
    _JWKS_CACHE[_CACHE_KEY] = jwks
    return jwks


def _has_kid(jwks: dict[str, Any], kid: str | None) -> bool:
    return any(key.get("kid") == kid for key in jwks.get("keys", []))


def verify_token(token: str) -> dict[str, Any]:
    """Verify an Auth0 JWT (signature + issuer/audience/expiry); return its claims."""
    settings = get_settings()
    try:
        kid = jwt.get_unverified_header(token).get("kid")
    except JWTError as exc:
        raise AuthenticationError("Malformed token") from exc

    jwks = _get_jwks()
    if not _has_kid(jwks, kid):
        # Invalidation path: a rotated key we haven't cached — refetch once.
        jwks = _get_jwks(force_refresh=True)

    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            jwks,
            algorithms=settings.auth0_algorithms,
            audience=settings.auth0_audience,
            issuer=_issuer(settings),
        )
    except JWTError as exc:
        raise AuthenticationError("Token verification failed") from exc
    return claims
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_auth_token.py -v`
Expected: PASS (7 tests).

- [ ] **Step 6: Commit**

```bash
git add auth/token.py tests/helpers/auth.py tests/unit/test_auth_token.py
git commit -m "feat: add Auth0 JWT verification with cached JWKS"
```

---

## Task 6: `User` ORM model

**Files:**
- Modify: `auth/models.py` (currently empty)
- Test: `tests/unit/test_auth_model.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_auth_model.py`:

```python
"""Unit tests for the User ORM model — structure only, no DB connection."""

from auth.models import User


def test_user_table_name() -> None:
    assert User.__tablename__ == "users"


def test_user_columns_and_constraints() -> None:
    cols = User.__table__.columns
    assert cols["auth0_sub"].unique is True
    assert cols["auth0_sub"].nullable is False
    assert cols["email"].nullable is False
    assert cols["role"].nullable is False
    # one user per tenant for now; admins have NULL tenant_id
    assert cols["tenant_id"].nullable is True
    assert cols["tenant_id"].unique is True
    assert any(fk.column.table.name == "tenants" for fk in cols["tenant_id"].foreign_keys)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_auth_model.py -v`
Expected: FAIL — `ImportError: cannot import name 'User' from 'auth.models'`.

- [ ] **Step 3: Write minimal implementation**

Write `auth/models.py`:

```python
"""The User ORM model (internal to auth).

Other code uses auth.service and auth.schemas, never this model directly. The
Role enum lives in schemas.py and is reused here.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Uuid, func
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column

from auth.schemas import Role
from core.db import Base


class User(Base):
    """A human who authenticated via Auth0; keyed by the Auth0 subject claim."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    auth0_sub: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    email: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[Role] = mapped_column(SQLEnum(Role, name="user_role"), nullable=False)
    # Nullable + unique: NULL for platform_admins (Postgres allows many NULLs),
    # at most one user per tenant for now.
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("tenants.id"), nullable=True, unique=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_auth_model.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add auth/models.py tests/unit/test_auth_model.py
git commit -m "feat: add User ORM model"
```

---

## Task 7: `users` migration

**Files:**
- Modify: `migrations/env.py`
- Create: `migrations/versions/<generated>_create_users_table.py`

> Requires no DB to author (`alembic revision` without `--autogenerate` does not connect). Applying it (`make migrate`) needs Postgres.

- [ ] **Step 1: Register the model for metadata**

In `migrations/env.py`, add below the existing `import shared.tenant.models` line:

```python
import auth.models  # noqa: F401  (register the users table on Base.metadata)
```

- [ ] **Step 2: Generate an empty revision file**

Run: `uv run alembic revision -m "create users table"`
Expected: prints `Generating .../migrations/versions/<hash>_create_users_table.py ... done`. (No DB connection needed.)

- [ ] **Step 3: Fill in the migration body**

In the new file, set the down-revision and replace `upgrade`/`downgrade`:

```python
down_revision: str | None = "7c984ef0d9e8"
```

```python
def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("auth0_sub", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("role", sa.Enum("PLATFORM_ADMIN", "TENANT", name="user_role"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.UniqueConstraint("auth0_sub"),
        sa.UniqueConstraint("tenant_id"),
    )


def downgrade() -> None:
    op.drop_table("users")
```

- [ ] **Step 4: Apply the migration (requires Postgres)**

Run: `make migrate`
Expected: `Running upgrade 7c984ef0d9e8 -> <hash>, create users table`.

- [ ] **Step 5: Commit**

```bash
git add migrations/env.py migrations/versions/
git commit -m "feat: add users table migration"
```

---

## Task 8: `get_or_create_user` service

**Files:**
- Modify: `auth/service.py` (currently empty)
- Test: `tests/integration/test_auth_service.py` (requires Postgres)

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_auth_service.py`:

```python
"""Integration tests for auth.service.get_or_create_user — real Postgres."""

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from auth.models import User
from auth.schemas import Principal, Role
from auth.service import get_or_create_user
from shared.tenant.schemas import BusinessType, TenantCreate
from shared.tenant.service import create_tenant


def _admin_principal(sub: str) -> Principal:
    return Principal(subject=sub, email=f"{sub}@us.com", tenant_id=None, role=Role.PLATFORM_ADMIN)


async def test_creates_user_on_first_call_then_returns_same(session: AsyncSession) -> None:
    principal = _admin_principal("auth0|first")
    created = await get_or_create_user(session, principal)
    again = await get_or_create_user(session, principal)

    assert created.id == again.id
    rows = (await session.execute(select(User))).scalars().all()
    assert len(rows) == 1


async def test_admin_user_has_null_tenant_and_multiple_admins_allowed(session: AsyncSession) -> None:
    a = await get_or_create_user(session, _admin_principal("auth0|a"))
    b = await get_or_create_user(session, _admin_principal("auth0|b"))
    assert a.tenant_id is None
    assert b.tenant_id is None
    assert a.id != b.id


async def test_tenant_user_links_to_tenant(session: AsyncSession) -> None:
    tenant = await create_tenant(
        session,
        TenantCreate(
            company_name="Acme",
            primary_contact_name="Ada",
            primary_contact_email="ada@acme.com",
            business_type=BusinessType.B2B,
        ),
    )
    principal = Principal(
        subject="auth0|tenantuser", email="ada@acme.com", tenant_id=tenant.id, role=Role.TENANT
    )
    user = await get_or_create_user(session, principal)
    assert user.tenant_id == tenant.id
    assert user.role is Role.TENANT


async def test_second_user_for_same_tenant_violates_unique(session: AsyncSession) -> None:
    tenant = await create_tenant(
        session,
        TenantCreate(
            company_name="Acme",
            primary_contact_name="Ada",
            primary_contact_email="ada@acme.com",
            business_type=BusinessType.B2B,
        ),
    )
    await get_or_create_user(
        session,
        Principal(subject="auth0|u1", email="u1@acme.com", tenant_id=tenant.id, role=Role.TENANT),
    )
    with pytest.raises(IntegrityError):
        await get_or_create_user(
            session,
            Principal(subject="auth0|u2", email="u2@acme.com", tenant_id=tenant.id, role=Role.TENANT),
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_auth_service.py -v`
Expected: FAIL — `ImportError: cannot import name 'get_or_create_user' from 'auth.service'`.

- [ ] **Step 3: Write minimal implementation**

Write `auth/service.py`:

```python
"""Public service for users — the only path to materialise a User from a Principal."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth.models import User
from auth.schemas import Principal


async def get_or_create_user(session: AsyncSession, principal: Principal) -> User:
    """Return the User for this principal, creating it on first sight.

    Auth0 is the source of truth: an existing row's email/role/tenant_id are
    refreshed from the principal so changes in Auth0 propagate on next login.
    """
    result = await session.execute(select(User).where(User.auth0_sub == principal.subject))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(
            auth0_sub=principal.subject,
            email=principal.email,
            role=principal.role,
            tenant_id=principal.tenant_id,
        )
        session.add(user)
    else:
        user.email = principal.email
        user.role = principal.role
        user.tenant_id = principal.tenant_id
    await session.commit()
    await session.refresh(user)
    return user
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_auth_service.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add auth/service.py tests/integration/test_auth_service.py
git commit -m "feat: add get_or_create_user service"
```

---

## Task 9: `AppError` exception handler

**Files:**
- Modify: `api/middleware.py` (currently empty)
- Test: `tests/unit/test_app_error_handler.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_app_error_handler.py`:

```python
"""Unit test: AppError subclasses map to their status_code over HTTP."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.middleware import app_error_handler
from core.exceptions import AppError, AuthenticationError


def _app() -> FastAPI:
    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)

    @app.get("/boom")
    async def boom() -> None:
        raise AuthenticationError("nope")

    return app


def test_app_error_handler_maps_status_and_message() -> None:
    client = TestClient(_app())
    resp = client.get("/boom")
    assert resp.status_code == 401
    assert resp.json() == {"detail": "nope"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_app_error_handler.py -v`
Expected: FAIL — `ImportError: cannot import name 'app_error_handler' from 'api.middleware'`.

- [ ] **Step 3: Write minimal implementation**

Write `api/middleware.py`:

```python
"""HTTP error wiring: turn any AppError into a JSON response by its status_code."""

from fastapi import Request
from fastapi.responses import JSONResponse

from core.exceptions import AppError


async def app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Map an AppError to a JSON response carrying its status_code and message."""
    assert isinstance(exc, AppError)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_app_error_handler.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/middleware.py tests/unit/test_app_error_handler.py
git commit -m "feat: add AppError-to-JSON exception handler"
```

---

## Task 10: `get_current_user` dependency, `/me` endpoint, app wiring

**Files:**
- Modify: `auth/dependencies.py` (currently empty)
- Create: `api/me.py`
- Modify: `main.py`
- Test: `tests/integration/test_me_endpoint.py` (requires Postgres)

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_me_endpoint.py`:

```python
"""Integration tests for GET /me — TestClient with token verification patched."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import auth.token as token_module
from auth.models import User
from main import app

NS = "https://leadengine/"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A TestClient whose token verification is stubbed by the Bearer string."""

    def fake_verify(token: str) -> dict[str, Any]:
        if token == "admin-token":
            return {"sub": "auth0|admin", "email": "ops@us.com", f"{NS}role": "PLATFORM_ADMIN"}
        from core.exceptions import AuthenticationError

        raise AuthenticationError("bad token")

    monkeypatch.setattr(token_module, "verify_token", fake_verify)
    return TestClient(app)


async def test_me_with_valid_token_returns_user_and_persists(
    client: TestClient, session: AsyncSession
) -> None:
    resp = client.get("/me", headers={"Authorization": "Bearer admin-token"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "ops@us.com"
    assert body["role"] == "PLATFORM_ADMIN"
    assert body["tenant_id"] is None

    rows = (await session.execute(select(User).where(User.auth0_sub == "auth0|admin"))).scalars().all()
    assert len(rows) == 1


def test_me_without_token_is_401(client: TestClient) -> None:
    resp = client.get("/me")
    assert resp.status_code == 401


def test_me_with_invalid_token_is_401(client: TestClient) -> None:
    resp = client.get("/me", headers={"Authorization": "Bearer garbage"})
    assert resp.status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_me_endpoint.py -v`
Expected: FAIL — `ImportError` (no `get_current_user`) or 404 on `/me`.

- [ ] **Step 3: Write the dependency**

Write `auth/dependencies.py`:

```python
"""FastAPI auth dependencies: resolve the current User from the bearer token."""

from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from auth import token as token_module
from auth.models import User
from auth.schemas import Principal
from auth.service import get_or_create_user
from core.config import get_settings
from core.db import get_session
from core.exceptions import AuthenticationError

_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    """Validate the bearer token, then find-or-create and return the local User."""
    if credentials is None:
        raise AuthenticationError("Missing bearer token")
    claims = token_module.verify_token(credentials.credentials)
    principal = Principal.from_claims(claims, get_settings().auth_claim_namespace)
    return await get_or_create_user(session, principal)
```

- [ ] **Step 4: Write the `/me` router**

Create `api/me.py`:

```python
"""The /me endpoint — returns the currently authenticated user."""

from typing import Annotated

from fastapi import APIRouter, Depends

from auth.dependencies import get_current_user
from auth.models import User
from auth.schemas import UserRead

router = APIRouter()


@router.get("/me", response_model=UserRead)
async def read_me(user: Annotated[User, Depends(get_current_user)]) -> User:
    """Return the authenticated user's record."""
    return user
```

- [ ] **Step 5: Wire into `main.py`**

Modify `main.py` to register the handler and include the router:

```python
"""Application entry point. Builds the FastAPI app and exposes /health and /me."""

from fastapi import FastAPI

from api.me import router as me_router
from api.middleware import app_error_handler
from core.exceptions import AppError
from core.lifespan import lifespan

app = FastAPI(title="Lead Intelligence Engine", version="0.1.0", lifespan=lifespan)
app.add_exception_handler(AppError, app_error_handler)
app.include_router(me_router)


@app.get("/health")
async def health() -> dict[str, str]:
    """Health check endpoint used by Docker, CI, and load balancers."""
    return {"status": "ok"}
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_me_endpoint.py -v`
Expected: PASS (3 tests).

- [ ] **Step 7: Commit**

```bash
git add auth/dependencies.py api/me.py main.py tests/integration/test_me_endpoint.py
git commit -m "feat: add get_current_user dependency and GET /me endpoint"
```

---

## Task 11: Full-suite gate

**Files:** none (verification only)

- [ ] **Step 1: Run the local CI gate**

Run: `make ci`
Expected: PASS — `ruff check .` clean, `mypy .` (strict) clean, full pytest suite green (prior tests + the new auth unit + integration tests). Integration/API tests require Postgres; if it isn't running, run unit-only with `make test-unit` and note the integration tests as DB-pending.

- [ ] **Step 2: If anything fails, fix it**

Likely issues and fixes:
- `ruff` import ordering on new files — run `make format`, then re-run `make ci`.
- `mypy` on `main.py`'s `add_exception_handler` — the handler's `exc: Exception` signature (Task 9) is what satisfies Starlette's expected type; keep it.
- If `mypy` flags an unused `# type: ignore`, remove it; if it flags a real error, address it directly.

- [ ] **Step 3: Commit any fixes**

```bash
git add -A
git commit -m "chore: satisfy lint/typecheck for auth slice"
```

(Skip if Step 1 passed clean.)

---

## Self-Review Notes

- **Spec coverage:** Auth0 settings (T3) · `AuthenticationError` (T2) · JWKS-cached JWT verification incl. TTL + unknown-kid refetch invalidation (T5) · `Role`/`Principal`/`from_claims`/`UserRead` (T4) · `User` table with unique `auth0_sub`, nullable+unique `tenant_id` FK (T6) · migration (T7) · find-or-create with claim sync (T8) · `AppError`→HTTP handler (T9) · `get_current_user` + `/me` + app wiring (T10) · obsolete files removed (T1) · `permissions.py` left empty / authz & KYC deferred (no task — intentional) · all error edge cases produce 401 (T5 raises `AuthenticationError`, T9/T10 map it). Full gate (T11).
- **Placeholders:** none — every code step shows complete code.
- **Type consistency:** `verify_token(token: str) -> dict[str, Any]` used the same in T5/T10; `Principal.from_claims(claims, namespace)` consistent T4/T10; `get_or_create_user(session, principal) -> User` consistent T8/T10; `app_error_handler(request, exc: Exception)` consistent T9/T10; `Role` values `PLATFORM_ADMIN`/`TENANT` consistent across schema, model, migration, tests.
