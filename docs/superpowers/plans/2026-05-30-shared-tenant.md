# shared/tenant Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `shared/tenant` — the bedrock tenant entity, status lifecycle, and public service — as the project's first ORM model, first Alembic migration, and first integration tests.

**Architecture:** A `Tenant` SQLAlchemy model (internal) + Pydantic `schemas.py` (public, holds the enums) + an async `service.py` (public, the only mutation path). Schemas are unit-tested with no DB; the model and service are integration-tested against a real Dockerized Postgres with the schema applied via Alembic. Alembic runs **synchronously** via the already-installed `psycopg2` driver (the app keeps using async `asyncpg` at runtime).

**Tech Stack:** Python 3.13, SQLAlchemy 2.0 (async + Alembic sync), Pydantic v2 (`EmailStr`), pytest (`asyncio_mode=auto`), Postgres 16 (Docker).

**Pre-flight (already verified this session):** Docker Postgres `enrichment-postgres-1` is healthy and accepting connections; `asyncpg`/`greenlet`/`alembic`/`sqlalchemy` installed. Work happens on branch `feature/shared-tenant`. Spec: `docs/superpowers/specs/2026-05-30-shared-tenant-design.md`.

---

## File structure

| File | Responsibility |
|---|---|
| `pyproject.toml` (modify) | Add `pydantic[email]`; exclude `migrations/versions` from mypy/ruff |
| `shared/tenant/schemas.py` (create) | Public enums + `TenantCreate` / `TenantRead` |
| `shared/tenant/models.py` (create) | Internal `Tenant` ORM model |
| `migrations/env.py` (create) | Alembic runtime, wired to `core.db.Base.metadata` (sync psycopg2) |
| `migrations/script.py.mako` (create) | Alembic migration file template |
| `migrations/versions/*.py` (generated) | First migration: `tenants` table |
| `tests/unit/test_tenant_schemas.py` (create) | Unit tests for schemas (no DB) |
| `tests/unit/test_tenant_model.py` (create) | Metadata sanity tests for the model (no DB) |
| `tests/integration/conftest.py` (create) | Apply migrations once; per-test session + cleanup |
| `tests/integration/test_tenant_service.py` (create) | Integration tests for service functions |
| `shared/tenant/service.py` (create) | Public async service: create/get/activate/is_active |

---

## Task 1: Add the email-validator dependency and exclude generated migrations from linters

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Switch pydantic to the `email` extra**

In `pyproject.toml`, change line 11 from:

```toml
    "pydantic>=2.9",
```

to:

```toml
    "pydantic[email]>=2.9",
```

- [ ] **Step 2: Exclude generated migration files from mypy and ruff**

Generated migration files in `migrations/versions/` should not be held to strict typing/lint. In `pyproject.toml`, add an `exclude` to `[tool.mypy]` and an `extend-exclude` to `[tool.ruff]`.

Change the `[tool.mypy]` block to:

```toml
[tool.mypy]
python_version = "3.13"
strict = true
ignore_missing_imports = true
disallow_untyped_decorators = false
exclude = ["migrations/versions/"]
```

Change the `[tool.ruff]` block to:

```toml
[tool.ruff]
line-length = 100
target-version = "py313"
extend-exclude = ["migrations/versions"]
```

- [ ] **Step 3: Sync the environment**

Run: `uv sync`
Expected: resolves and installs `email-validator` (a new package) with no errors.

- [ ] **Step 4: Verify email-validator is importable**

Run: `uv run python -c "import email_validator; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: add pydantic[email] and exclude generated migrations from linters"
```

---

## Task 2: Tenant schemas (public surface) — unit tested, no DB

**Files:**
- Create: `shared/tenant/schemas.py`
- Test: `tests/unit/test_tenant_schemas.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_tenant_schemas.py`:

```python
"""Unit tests for shared.tenant.schemas — pure validation, no DB."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from shared.tenant.schemas import (
    BusinessType,
    TenantCreate,
    TenantRead,
    TenantStatus,
)


def test_business_type_membership_is_exactly_b2b_and_b2c() -> None:
    assert {m.value for m in BusinessType} == {"B2B", "B2C"}


def test_tenant_status_membership_is_exact() -> None:
    assert {m.value for m in TenantStatus} == {
        "CREATED",
        "ACTIVE",
        "SUSPENDED",
        "CHURNED",
    }


def test_tenant_create_accepts_valid_input() -> None:
    data = TenantCreate(
        company_name="Gamoft",
        primary_contact_name="Asha",
        primary_contact_email="asha@gamoft.com",
        business_type=BusinessType.B2B,
    )
    assert data.business_type is BusinessType.B2B
    # timezone/language fall back to defaults
    assert data.timezone == "UTC"
    assert data.language_preference == "en"


def test_tenant_create_rejects_invalid_business_type() -> None:
    # model_validate takes Any, so an invalid value is a runtime (not type) error.
    with pytest.raises(ValidationError):
        TenantCreate.model_validate(
            {
                "company_name": "Gamoft",
                "primary_contact_name": "Asha",
                "primary_contact_email": "asha@gamoft.com",
                "business_type": "B2X",
            }
        )


def test_tenant_create_rejects_invalid_email() -> None:
    with pytest.raises(ValidationError):
        TenantCreate(
            company_name="Gamoft",
            primary_contact_name="Asha",
            primary_contact_email="not-an-email",
            business_type=BusinessType.B2B,
        )


def test_tenant_create_rejects_missing_required_field() -> None:
    with pytest.raises(ValidationError):
        TenantCreate.model_validate(
            {
                "company_name": "Gamoft",
                "primary_contact_name": "Asha",
                "business_type": "B2B",
            }
        )


def test_tenant_read_builds_from_orm_like_object() -> None:
    obj = SimpleNamespace(
        id=uuid4(),
        company_name="Gamoft",
        primary_contact_name="Asha",
        primary_contact_email="asha@gamoft.com",
        business_type=BusinessType.B2B,
        status=TenantStatus.CREATED,
        timezone="UTC",
        language_preference="en",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        activated_at=None,
    )
    read = TenantRead.model_validate(obj)
    assert read.company_name == "Gamoft"
    assert read.status is TenantStatus.CREATED
    assert read.activated_at is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_tenant_schemas.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shared.tenant.schemas'`.

- [ ] **Step 3: Write the schemas**

Create `shared/tenant/schemas.py`:

```python
"""Public schemas and enums for the tenant module.

These are the single source of truth for tenant enums; api/, models.py, and
tests import them from here.
"""

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr


class BusinessType(str, Enum):
    """Whether the tenant sells to businesses or consumers."""

    B2B = "B2B"
    B2C = "B2C"


class TenantStatus(str, Enum):
    """The tenant lifecycle. 'Onboarding' is simply CREATED."""

    CREATED = "CREATED"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    CHURNED = "CHURNED"


class TenantCreate(BaseModel):
    """Fields a caller provides to create a tenant. The system assigns the rest."""

    company_name: str
    primary_contact_name: str
    primary_contact_email: EmailStr
    business_type: BusinessType
    timezone: str = "UTC"
    language_preference: str = "en"


class TenantRead(BaseModel):
    """The full tenant record returned to callers; built from the ORM object."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company_name: str
    primary_contact_name: str
    primary_contact_email: EmailStr
    business_type: BusinessType
    status: TenantStatus
    timezone: str
    language_preference: str
    created_at: datetime
    updated_at: datetime
    activated_at: datetime | None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_tenant_schemas.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Typecheck and commit**

Run: `uv run mypy shared/tenant/schemas.py tests/unit/test_tenant_schemas.py`
Expected: `Success: no issues found`.

```bash
git add shared/tenant/schemas.py tests/unit/test_tenant_schemas.py
git commit -m "feat: add tenant schemas and enums with unit tests"
```

---

## Task 3: Tenant ORM model (internal) — metadata sanity tests, no DB

**Files:**
- Create: `shared/tenant/models.py`
- Test: `tests/unit/test_tenant_model.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_tenant_model.py`:

```python
"""Metadata-level sanity tests for the Tenant model — no DB connection."""

from core.db import Base
from shared.tenant.models import Tenant


def test_tenant_table_is_registered_on_metadata() -> None:
    assert "tenants" in Base.metadata.tables


def test_tenant_has_the_expected_columns() -> None:
    columns = {column.name for column in Tenant.__table__.columns}
    assert columns == {
        "id",
        "company_name",
        "primary_contact_name",
        "primary_contact_email",
        "business_type",
        "status",
        "timezone",
        "language_preference",
        "created_at",
        "updated_at",
        "activated_at",
    }


def test_id_is_primary_key_and_activated_at_is_nullable() -> None:
    table = Tenant.__table__
    assert table.c.id.primary_key is True
    assert table.c.activated_at.nullable is True
    assert table.c.company_name.nullable is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_tenant_model.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shared.tenant.models'`.

- [ ] **Step 3: Write the model**

Create `shared/tenant/models.py`:

```python
"""The Tenant ORM model (internal to the tenant module).

Other modules never import this; they use shared.tenant.service and
shared.tenant.schemas. Enums are defined in schemas.py and reused here.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum as SQLEnum, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from core.db import Base
from shared.tenant.schemas import BusinessType, TenantStatus


class Tenant(Base):
    """A business account / client workspace that owns all its data."""

    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    company_name: Mapped[str] = mapped_column(String, nullable=False)
    primary_contact_name: Mapped[str] = mapped_column(String, nullable=False)
    primary_contact_email: Mapped[str] = mapped_column(String, nullable=False)
    business_type: Mapped[BusinessType] = mapped_column(
        SQLEnum(BusinessType, name="business_type"), nullable=False
    )
    status: Mapped[TenantStatus] = mapped_column(
        SQLEnum(TenantStatus, name="tenant_status"),
        nullable=False,
        default=TenantStatus.CREATED,
    )
    timezone: Mapped[str] = mapped_column(String, nullable=False, default="UTC")
    language_preference: Mapped[str] = mapped_column(
        String, nullable=False, default="en"
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
    activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_tenant_model.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Typecheck and commit**

Run: `uv run mypy shared/tenant/models.py tests/unit/test_tenant_model.py`
Expected: `Success: no issues found`.

```bash
git add shared/tenant/models.py tests/unit/test_tenant_model.py
git commit -m "feat: add Tenant ORM model with metadata sanity tests"
```

---

## Task 4: Stand up Alembic (sync env.py + script template)

**Files:**
- Create: `migrations/env.py`
- Create: `migrations/script.py.mako`

Context: `alembic.ini` already exists (`script_location = migrations`, `prepend_sys_path = .`). Only `env.py` and the template are missing. Alembic runs synchronously via `psycopg2`, so `env.py` rewrites the `+asyncpg` URL to `+psycopg2`.

- [ ] **Step 1: Write the migration file template**

Create `migrations/script.py.mako`:

```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

# revision identifiers, used by Alembic.
revision: str = ${repr(up_revision)}
down_revision: str | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

- [ ] **Step 2: Write the Alembic runtime**

Create `migrations/env.py`:

```python
"""Alembic runtime, wired to the application's models.

Alembic runs synchronously (psycopg2); the app uses asyncpg at runtime, so we
rewrite the driver in the URL here. Importing shared.tenant.models registers the
Tenant table on Base.metadata so autogenerate can see it.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

import shared.tenant.models  # noqa: F401  (register models on Base.metadata)
from core.config import get_settings
from core.db import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The app's DATABASE_URL uses asyncpg; Alembic needs a sync driver.
sync_url = get_settings().database_url.replace("+asyncpg", "+psycopg2")
config.set_main_option("sqlalchemy.url", sync_url)

target_metadata = Base.metadata


def run_migrations_online() -> None:
    """Run migrations against a live database connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section) or {},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


run_migrations_online()
```

- [ ] **Step 3: Verify Alembic loads env.py and connects to Postgres**

Ensure Postgres is up first: `docker compose up -d postgres`

Run: `uv run alembic current`
Expected: exits 0 with no traceback (prints nothing or a blank current revision — there is no version table yet). A traceback here means env.py failed to load or connect.

- [ ] **Step 4: Typecheck env.py**

Run: `uv run mypy migrations/env.py`
Expected: `Success: no issues found` (env.py is checked; only `migrations/versions/` is excluded).

- [ ] **Step 5: Commit**

```bash
git add migrations/env.py migrations/script.py.mako
git commit -m "feat: wire Alembic env.py to Base.metadata (sync psycopg2)"
```

---

## Task 5: Generate and apply the first migration (tenants table)

**Files:**
- Generated: `migrations/versions/<hash>_create_tenants_table.py`

- [ ] **Step 1: Autogenerate the migration**

Run: `uv run alembic revision --autogenerate -m "create tenants table"`
Expected: creates a file under `migrations/versions/`; console shows `Detected added table 'tenants'`.

- [ ] **Step 2: Inspect the generated migration**

Open the new file in `migrations/versions/`. Confirm `upgrade()` calls `op.create_table("tenants", ...)` with all eleven columns and that `downgrade()` calls `op.drop_table("tenants")`. If `upgrade()`/`downgrade()` are empty (`pass`), autogenerate did not see the model — stop and check that `import shared.tenant.models` is present in `env.py`.

- [ ] **Step 3: Apply the migration**

Run: `uv run alembic upgrade head`
Expected: console shows `Running upgrade  -> <hash>, create tenants table`.

- [ ] **Step 4: Verify the table exists in Postgres**

Run: `docker exec enrichment-postgres-1 psql -U postgres -d leadengine -c "\dt"`
Expected: the output lists a `tenants` table (and `alembic_version`).

- [ ] **Step 5: Commit**

```bash
git add migrations/versions/
git commit -m "feat: add first migration creating the tenants table"
```

---

## Task 6: Integration test harness (conftest)

**Files:**
- Create: `tests/integration/conftest.py`

This applies migrations once per test session and gives each test a clean `AsyncSession`, deleting rows afterward so tests stay isolated (the service commits, so we clean by truncating rather than relying on a rollback).

- [ ] **Step 1: Write the conftest**

Create `tests/integration/conftest.py`:

```python
"""Fixtures for integration tests: a migrated Postgres and a clean session per test."""

import subprocess
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.config import get_settings
from core.db import Base


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations() -> None:
    """Bring the test database schema up to head once for the whole session."""
    subprocess.run(["uv", "run", "alembic", "upgrade", "head"], check=True)


@pytest.fixture
async def session() -> AsyncGenerator[AsyncSession]:
    """Yield an AsyncSession, then wipe all tables so the next test starts empty."""
    engine = create_async_engine(get_settings().database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db_session:
            yield db_session
    finally:
        async with engine.begin() as conn:
            for table in reversed(Base.metadata.sorted_tables):
                await conn.execute(
                    text(f'TRUNCATE TABLE "{table.name}" RESTART IDENTITY CASCADE')
                )
        await engine.dispose()
```

- [ ] **Step 2: Verify the harness imports and migrations run (via the next task's first test)**

There is nothing to run standalone yet; the conftest is exercised by Task 7. Proceed to Task 7, whose first test run confirms `_apply_migrations` and `session` work.

- [ ] **Step 3: Typecheck and commit**

Run: `uv run mypy tests/integration/conftest.py`
Expected: `Success: no issues found`.

```bash
git add tests/integration/conftest.py
git commit -m "test: add integration conftest (migrated db + clean session per test)"
```

---

## Task 7: Tenant service (public) — integration tested

**Files:**
- Create: `shared/tenant/service.py`
- Test: `tests/integration/test_tenant_service.py`

- [ ] **Step 1: Write the failing integration tests**

Create `tests/integration/test_tenant_service.py`:

```python
"""Integration tests for shared.tenant.service against a real Postgres."""

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import ConflictError, NotFoundError
from shared.tenant import service
from shared.tenant.schemas import BusinessType, TenantCreate, TenantStatus


def _sample_create() -> TenantCreate:
    return TenantCreate(
        company_name="Gamoft",
        primary_contact_name="Asha",
        primary_contact_email="asha@gamoft.com",
        business_type=BusinessType.B2B,
    )


async def test_create_tenant_persists_with_created_status(session: AsyncSession) -> None:
    tenant = await service.create_tenant(session, _sample_create())

    assert tenant.id is not None
    assert tenant.status is TenantStatus.CREATED
    assert tenant.activated_at is None
    assert tenant.created_at is not None


async def test_get_tenant_returns_the_created_tenant(session: AsyncSession) -> None:
    created = await service.create_tenant(session, _sample_create())

    fetched = await service.get_tenant(session, created.id)

    assert fetched.id == created.id
    assert fetched.company_name == "Gamoft"


async def test_get_tenant_missing_raises_not_found(session: AsyncSession) -> None:
    with pytest.raises(NotFoundError):
        await service.get_tenant(session, uuid4())


async def test_activate_tenant_sets_active_and_activated_at(session: AsyncSession) -> None:
    created = await service.create_tenant(session, _sample_create())

    activated = await service.activate_tenant(session, created.id)

    assert activated.status is TenantStatus.ACTIVE
    assert activated.activated_at is not None


async def test_activate_tenant_twice_raises_conflict(session: AsyncSession) -> None:
    created = await service.create_tenant(session, _sample_create())
    await service.activate_tenant(session, created.id)

    with pytest.raises(ConflictError):
        await service.activate_tenant(session, created.id)


async def test_is_active_reflects_status(session: AsyncSession) -> None:
    created = await service.create_tenant(session, _sample_create())
    assert service.is_active(created) is False

    activated = await service.activate_tenant(session, created.id)
    assert service.is_active(activated) is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/integration/test_tenant_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shared.tenant.service'` (after `_apply_migrations` runs `alembic upgrade head` successfully — confirming the harness works).

- [ ] **Step 3: Write the service**

Create `shared/tenant/service.py`:

```python
"""Public service for tenants — the only path to create, read, and activate them."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import ConflictError, NotFoundError
from shared.tenant.models import Tenant
from shared.tenant.schemas import TenantCreate, TenantStatus


async def create_tenant(session: AsyncSession, data: TenantCreate) -> Tenant:
    """Create a tenant in the CREATED state and return it."""
    tenant = Tenant(
        company_name=data.company_name,
        primary_contact_name=data.primary_contact_name,
        primary_contact_email=data.primary_contact_email,
        business_type=data.business_type,
        timezone=data.timezone,
        language_preference=data.language_preference,
        status=TenantStatus.CREATED,
    )
    session.add(tenant)
    await session.commit()
    await session.refresh(tenant)
    return tenant


async def get_tenant(session: AsyncSession, tenant_id: UUID) -> Tenant:
    """Return the tenant or raise NotFoundError."""
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise NotFoundError(f"Tenant {tenant_id} not found")
    return tenant


async def activate_tenant(session: AsyncSession, tenant_id: UUID) -> Tenant:
    """Move a CREATED tenant to ACTIVE. Raise ConflictError if not CREATED."""
    tenant = await get_tenant(session, tenant_id)
    if tenant.status is not TenantStatus.CREATED:
        raise ConflictError(
            f"Tenant {tenant_id} cannot be activated from status {tenant.status.value}"
        )
    tenant.status = TenantStatus.ACTIVE
    tenant.activated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(tenant)
    return tenant


def is_active(tenant: Tenant) -> bool:
    """The activation gate other layers consult."""
    return tenant.status is TenantStatus.ACTIVE
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/integration/test_tenant_service.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Typecheck and commit**

Run: `uv run mypy shared/tenant/service.py tests/integration/test_tenant_service.py`
Expected: `Success: no issues found`.

```bash
git add shared/tenant/service.py tests/integration/test_tenant_service.py
git commit -m "feat: add tenant service with integration tests"
```

---

## Task 8: Full local CI gate

**Files:** none (verification only)

- [ ] **Step 1: Run the whole gate**

Run: `make ci`
Expected: `ruff check` clean, `mypy .` reports `Success`, and the full pytest suite passes (existing core tests + new tenant unit and integration tests). Postgres must be up for the integration tier.

- [ ] **Step 2: If anything fails, fix and re-run**

Address any lint/type/test failure, then re-run `make ci` until green. Do not proceed with a red gate.

- [ ] **Step 3: Final commit (only if fixes were needed)**

```bash
git add -A
git commit -m "chore: satisfy full CI gate for shared/tenant"
```

---

## Notes for the implementer

- **Enums create Postgres types.** The first migration will also create `business_type` and `tenant_status` enum types. That is expected.
- **`is_active` takes a loaded `Tenant`, not a session** — it is a pure status check with no DB round-trip. This asymmetry with the other functions is intentional.
- **Test isolation** is by truncation after each test, not transaction rollback, because the service commits. Keep it that way unless a future need justifies the savepoint pattern.
- **Don't push `feature/shared-tenant` or merge it yourself** — open a PR, wait for green CI, get a teammate's approval (project rule).
