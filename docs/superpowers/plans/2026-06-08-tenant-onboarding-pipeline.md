# Tenant Onboarding Pipeline — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the fully-automated three-agent pipeline (Persona → ICP → Signals) that runs after `POST /onboarding`, fetches the tenant's website, builds a `tenant_config`, and activates the tenant.

**Architecture:** A single ARQ background job is enqueued by `POST /onboarding`; it runs `modules/tenant_onboarding/pipeline.py` which calls three Sonnet agents in sequence via `clients/sonnet_client.py`, writes the result directly to `shared/tenant_config` as `ACTIVE`, and transitions the tenant to `ACTIVE`. The tenant polls `GET /me` for `onboarding_status`.

**Tech Stack:** FastAPI · SQLAlchemy 2.0 async · ARQ (Redis-backed job queue) · Anthropic Python SDK · httpx · pytest (asyncio_mode=auto) · mypy strict · ruff.

**Spec:** `docs/superpowers/specs/2026-06-08-tenant-onboarding-design.md`

> **Scope note:** This plan touches several layers (shared/, clients/, core/, workers/, api/). All changes are in service of one feature and interdependent, so they are kept in one plan. Tasks are ordered to unblock each other: shared changes first, then infrastructure, then agents, then wiring.

> **Environment:** Integration tests require real Postgres (`DATABASE_URL`) and Redis (`REDIS_URL`). Run `docker compose up -d` before running integration tests. Unit tests need neither.

---

## File Structure

**Modified:**
- `shared/tenant_config/schemas.py` — `ConfigStatus`: remove `DRAFT`, `REJECTED`
- `shared/tenant_config/models.py` — remove `uq_draft_config_per_tenant` index
- `shared/tenant_config/service.py` — remove `create_draft`/`approve_version`/`reject_version`; add `create_active`
- `shared/tenant/schemas.py` — add `OnboardingStatus` enum; add `website_url` to `TenantCreate`; add `website_url` + `onboarding_status` to `TenantRead`
- `shared/tenant/models.py` — add `website_url`, `onboarding_status` columns
- `shared/tenant/service.py` — add `set_onboarding_status`
- `core/config.py` — add `anthropic_api_key`, `redis_url`
- `core/queue.py` — implement ARQ pool (first real consumer)
- `core/lifespan.py` — init ARQ pool on startup, close on shutdown
- `api/onboarding.py` — enqueue ARQ job after creating tenant
- `workers/worker.py` — register `run_onboarding_pipeline` job
- `tests/integration/test_onboarding_endpoint.py` — add `website_url` to `_BUSINESS`; mock ARQ pool
- `tests/integration/test_tenant_config_service.py` — replace DRAFT/approve/reject tests with `create_active` test
- `tests/unit/test_tenant_config_schemas.py` — update `ConfigStatus` membership test

**Created:**
- `clients/sonnet_client.py` — thin async Anthropic SDK wrapper
- `modules/tenant_onboarding/service.py` — (empty module; `set_onboarding_status` lives in `shared/tenant/service.py`)
- `modules/tenant_onboarding/agents/__init__.py`
- `modules/tenant_onboarding/agents/persona.py`
- `modules/tenant_onboarding/agents/icp.py`
- `modules/tenant_onboarding/agents/signals.py`
- `modules/tenant_onboarding/pipeline.py`
- `workers/jobs/onboarding.py`
- `tests/unit/test_onboarding_agents.py`
- `tests/unit/test_onboarding_pipeline.py`
- Two Alembic migrations (ConfigStatus simplification + tenants columns)

---

## Task 1: Simplify ConfigStatus — remove DRAFT and REJECTED

**Files:**
- Modify: `shared/tenant_config/schemas.py`
- Modify: `shared/tenant_config/models.py`
- Modify: `shared/tenant_config/service.py`
- Modify: `tests/unit/test_tenant_config_schemas.py`
- Modify: `tests/integration/test_tenant_config_service.py`
- Create migration: `migrations/versions/<rev>_simplify_config_status.py`

- [ ] **Step 1: Update ConfigStatus enum**

In `shared/tenant_config/schemas.py`, replace the `ConfigStatus` class:

```python
class ConfigStatus(StrEnum):
    """The lifecycle state of a single tenant_config version."""

    ACTIVE   = "ACTIVE"
    ARCHIVED = "ARCHIVED"
```

- [ ] **Step 2: Remove DRAFT index from model**

In `shared/tenant_config/models.py`, remove the `uq_draft_config_per_tenant` index from `__table_args__`:

```python
    __table_args__ = (
        UniqueConstraint("tenant_id", "version", name="uq_tenant_config_version"),
        Index(
            "uq_active_config_per_tenant",
            "tenant_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )
```

- [ ] **Step 3: Replace service functions**

Replace the entire body of `shared/tenant_config/service.py` with:

```python
"""Public service for the tenant_config registry.

The pipeline writes configs directly as ACTIVE (no draft/approval step).
Re-running onboarding archives the previous ACTIVE and creates a new one.
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.tenant_config.models import TenantConfig
from shared.tenant_config.schemas import ConfigStatus, TenantConfigCreate, TenantConfigRead


async def get_active_config(session: AsyncSession, tenant_id: UUID) -> TenantConfigRead | None:
    """Return the tenant's single ACTIVE version, or None if it has none."""
    result = await session.execute(
        select(TenantConfig).where(
            TenantConfig.tenant_id == tenant_id,
            TenantConfig.status == ConfigStatus.ACTIVE,
        )
    )
    config = result.scalar_one_or_none()
    return TenantConfigRead.model_validate(config) if config is not None else None


async def create_active(
    session: AsyncSession, tenant_id: UUID, data: TenantConfigCreate
) -> TenantConfigRead:
    """Write a new ACTIVE config, archiving any previous ACTIVE version.

    The archive + insert are flushed before committing so the
    uq_active_config_per_tenant partial index is never momentarily violated.
    """
    now = datetime.now(UTC)
    existing = await session.execute(
        select(TenantConfig).where(
            TenantConfig.tenant_id == tenant_id,
            TenantConfig.status == ConfigStatus.ACTIVE,
        )
    )
    current_active = existing.scalar_one_or_none()
    if current_active is not None:
        current_active.status = ConfigStatus.ARCHIVED
        current_active.archived_at = now
        await session.flush()

    config = TenantConfig(
        tenant_id=tenant_id,
        version=await _next_version(session, tenant_id),
        status=ConfigStatus.ACTIVE,
        activated_at=now,
        business_profile=data.business_profile,
        icp=data.icp,
        signals=[s.model_dump() for s in data.signals],
        weights=data.weights.model_dump(),
        thresholds=data.thresholds.model_dump(),
    )
    session.add(config)
    await session.commit()
    await session.refresh(config)
    return TenantConfigRead.model_validate(config)


async def list_versions(session: AsyncSession, tenant_id: UUID) -> list[TenantConfigRead]:
    """Return all versions for a tenant, newest first."""
    result = await session.execute(
        select(TenantConfig)
        .where(TenantConfig.tenant_id == tenant_id)
        .order_by(TenantConfig.version.desc())
    )
    return [TenantConfigRead.model_validate(c) for c in result.scalars().all()]


async def _next_version(session: AsyncSession, tenant_id: UUID) -> int:
    """The next monotonic version number for a tenant (1 if none yet)."""
    result = await session.execute(
        select(func.max(TenantConfig.version)).where(TenantConfig.tenant_id == tenant_id)
    )
    return (result.scalar_one_or_none() or 0) + 1
```

- [ ] **Step 4: Update unit test for ConfigStatus membership**

In `tests/unit/test_tenant_config_schemas.py`, replace `test_config_status_membership_is_exact`:

```python
def test_config_status_membership_is_exact() -> None:
    assert {m.value for m in ConfigStatus} == {"ACTIVE", "ARCHIVED"}
```

Also update `test_tenant_config_read_builds_from_orm_like_object` — the `status` field uses `ConfigStatus.ACTIVE` already, so no change needed there.

- [ ] **Step 5: Rewrite integration tests for tenant_config service**

Replace the entire body of `tests/integration/test_tenant_config_service.py` with:

```python
"""Integration tests for shared.tenant_config.service against a real Postgres."""

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from shared.tenant import service as tenant_service
from shared.tenant.schemas import BusinessType, TenantCreate
from shared.tenant_config import service
from shared.tenant_config.schemas import ConfigStatus, TenantConfigCreate


def _signals() -> list[dict[str, str]]:
    return [
        {"id": "fit_1", "dimension": "FIT", "question": "In target industry?"},
        {"id": "intent_1", "dimension": "INTENT", "question": "Visited pricing?"},
        {"id": "eng_1", "dimension": "ENGAGEMENT", "question": "Opened last email?"},
        {"id": "beh_1", "dimension": "BEHAVIOUR", "question": "Requested a demo?"},
        {"id": "ctx_1", "dimension": "CONTEXT", "question": "Raised funding recently?"},
    ]


def _payload() -> TenantConfigCreate:
    return TenantConfigCreate.model_validate(
        {
            "business_profile": {"summary": "B2B SaaS"},
            "icp": {"summary": "Mid-market SaaS in APAC"},
            "signals": _signals(),
            "weights": {
                "fit": 0.2,
                "intent": 0.2,
                "engagement": 0.2,
                "behaviour": 0.2,
                "context": 0.2,
            },
            "thresholds": {"hot": 80, "warm": 55},
        }
    )


async def _make_tenant(session: AsyncSession) -> "UUID":
    from uuid import UUID
    tenant = await tenant_service.create_tenant(
        session,
        TenantCreate(
            company_name="Gamoft",
            primary_contact_name="Asha",
            primary_contact_email="asha@gamoft.com",
            business_type=BusinessType.B2B,
            website_url="https://gamoft.com",
        ),
    )
    return tenant.id


async def test_create_active_assigns_version_one(session: AsyncSession) -> None:
    tenant_id = await _make_tenant(session)

    config = await service.create_active(session, tenant_id, _payload())

    assert config.version == 1
    assert config.status is ConfigStatus.ACTIVE
    assert config.tenant_id == tenant_id
    assert config.activated_at is not None
    assert config.weights.fit == 0.2


async def test_create_active_archives_previous(session: AsyncSession) -> None:
    tenant_id = await _make_tenant(session)
    first = await service.create_active(session, tenant_id, _payload())

    second = await service.create_active(session, tenant_id, _payload())

    assert second.version == 2
    assert second.status is ConfigStatus.ACTIVE
    versions = {v.version: v.status for v in await service.list_versions(session, tenant_id)}
    assert versions[1] is ConfigStatus.ARCHIVED
    assert versions[2] is ConfigStatus.ACTIVE


async def test_get_active_config_returns_active(session: AsyncSession) -> None:
    tenant_id = await _make_tenant(session)
    created = await service.create_active(session, tenant_id, _payload())

    active = await service.get_active_config(session, tenant_id)

    assert active is not None
    assert active.id == created.id


async def test_get_active_config_returns_none_when_absent(session: AsyncSession) -> None:
    assert await service.get_active_config(session, uuid4()) is None


async def test_list_versions_returns_newest_first(session: AsyncSession) -> None:
    tenant_id = await _make_tenant(session)
    await service.create_active(session, tenant_id, _payload())
    await service.create_active(session, tenant_id, _payload())

    versions = await service.list_versions(session, tenant_id)

    assert [v.version for v in versions] == [2, 1]
```

- [ ] **Step 6: Write and apply the migration**

Create `migrations/versions/<new_rev>_simplify_config_status.py` (use `uv run alembic revision -m "simplify_config_status"` to generate the file, then fill in):

```python
"""simplify config_status enum to ACTIVE and ARCHIVED only

Revision ID: <generated>
Revises: 4ef9c33a98f6
Branch Labels: None
Depends on: None
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "<generated>"
down_revision: str | None = "4ef9c33a98f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("uq_draft_config_per_tenant", table_name="tenant_configs")
    op.execute("ALTER TYPE config_status RENAME TO config_status_old")
    op.execute("CREATE TYPE config_status AS ENUM ('ACTIVE', 'ARCHIVED')")
    op.execute(
        "ALTER TABLE tenant_configs ALTER COLUMN status TYPE config_status "
        "USING status::text::config_status"
    )
    op.execute("DROP TYPE config_status_old")


def downgrade() -> None:
    op.execute("ALTER TYPE config_status RENAME TO config_status_old")
    op.execute("CREATE TYPE config_status AS ENUM ('DRAFT', 'ACTIVE', 'ARCHIVED', 'REJECTED')")
    op.execute(
        "ALTER TABLE tenant_configs ALTER COLUMN status TYPE config_status "
        "USING status::text::config_status"
    )
    op.execute("DROP TYPE config_status_old")
    op.create_index(
        "uq_draft_config_per_tenant",
        "tenant_configs",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("status = 'DRAFT'"),
    )
```

Run: `uv run alembic upgrade head`

- [ ] **Step 7: Run tests**

```bash
uv run pytest tests/unit/test_tenant_config_schemas.py tests/integration/test_tenant_config_service.py -v
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add shared/tenant_config/ tests/unit/test_tenant_config_schemas.py tests/integration/test_tenant_config_service.py migrations/
git commit -m "feat: simplify ConfigStatus to ACTIVE/ARCHIVED only"
```

---

## Task 2: Add `website_url` and `onboarding_status` to tenants

**Files:**
- Modify: `shared/tenant/schemas.py`
- Modify: `shared/tenant/models.py`
- Modify: `shared/tenant/service.py`
- Modify: `tests/integration/test_tenant_service.py`
- Modify: `tests/integration/test_onboarding_endpoint.py`
- Modify: `tests/integration/test_tenant_config_service.py` (already updated in Task 1 to use `website_url`)
- Create migration

- [ ] **Step 1: Update `shared/tenant/schemas.py`**

Add `OnboardingStatus`; add `website_url` to `TenantCreate`; add `website_url` + `onboarding_status` to `TenantRead`:

```python
"""Public schemas and enums for the tenant module."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, EmailStr


class BusinessType(StrEnum):
    B2B = "B2B"
    B2C = "B2C"


class TenantStatus(StrEnum):
    CREATED   = "CREATED"
    ACTIVE    = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    CHURNED   = "CHURNED"


class OnboardingStatus(StrEnum):
    PENDING  = "PENDING"   # job queued, not yet picked up
    RUNNING  = "RUNNING"   # pipeline executing
    COMPLETE = "COMPLETE"  # tenant_config ACTIVE, tenant ACTIVE
    FAILED   = "FAILED"    # pipeline crashed; tenant can retry


class TenantCreate(BaseModel):
    company_name: str
    primary_contact_name: str
    primary_contact_email: EmailStr
    business_type: BusinessType
    website_url: AnyHttpUrl
    timezone: str = "UTC"
    language_preference: str = "en"


class TenantRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company_name: str
    primary_contact_name: str
    primary_contact_email: EmailStr
    business_type: BusinessType
    website_url: str
    onboarding_status: OnboardingStatus
    status: TenantStatus
    timezone: str
    language_preference: str
    created_at: datetime
    updated_at: datetime
    activated_at: datetime | None
```

- [ ] **Step 2: Update `shared/tenant/models.py`**

Add the two new columns and wire `website_url` and `onboarding_status` to `Tenant`. Also import `OnboardingStatus`:

```python
"""The Tenant ORM model (internal to the tenant module)."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Uuid, func
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column

from core.db import Base
from shared.tenant.schemas import BusinessType, OnboardingStatus, TenantStatus


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_name: Mapped[str] = mapped_column(String, nullable=False)
    primary_contact_name: Mapped[str] = mapped_column(String, nullable=False)
    primary_contact_email: Mapped[str] = mapped_column(String, nullable=False)
    business_type: Mapped[BusinessType] = mapped_column(
        SQLEnum(BusinessType, name="business_type"), nullable=False
    )
    website_url: Mapped[str] = mapped_column(String, nullable=False)
    onboarding_status: Mapped[OnboardingStatus] = mapped_column(
        String, nullable=False, default=OnboardingStatus.PENDING
    )
    status: Mapped[TenantStatus] = mapped_column(
        SQLEnum(TenantStatus, name="tenant_status"),
        nullable=False,
        default=TenantStatus.CREATED,
    )
    timezone: Mapped[str] = mapped_column(String, nullable=False, default="UTC")
    language_preference: Mapped[str] = mapped_column(String, nullable=False, default="en")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

- [ ] **Step 3: Update `shared/tenant/service.py` — add `set_onboarding_status`**

Add below `activate_tenant` (keep all existing functions):

```python
async def set_onboarding_status(
    session: AsyncSession, tenant_id: UUID, status: OnboardingStatus
) -> None:
    """Update the onboarding_status on the tenant row. No-op if tenant not found."""
    tenant = await session.get(Tenant, tenant_id)
    if tenant is not None:
        tenant.onboarding_status = status
        await session.commit()
```

Also add `OnboardingStatus` to the import from `shared.tenant.schemas`:

```python
from shared.tenant.schemas import OnboardingStatus, TenantCreate, TenantStatus
```

And update `create_tenant` to store `website_url`:

```python
async def create_tenant(session: AsyncSession, data: TenantCreate) -> Tenant:
    tenant = Tenant(
        company_name=data.company_name,
        primary_contact_name=data.primary_contact_name,
        primary_contact_email=data.primary_contact_email,
        business_type=data.business_type,
        website_url=str(data.website_url),
        timezone=data.timezone,
        language_preference=data.language_preference,
        status=TenantStatus.CREATED,
        onboarding_status=OnboardingStatus.PENDING,
    )
    session.add(tenant)
    await session.commit()
    await session.refresh(tenant)
    return tenant
```

- [ ] **Step 4: Write and apply the migration**

Run `uv run alembic revision -m "add_website_url_onboarding_status_to_tenants"`, then fill in:

```python
"""add website_url and onboarding_status to tenants

Revision ID: <generated>
Revises: <previous_rev>
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "<generated>"
down_revision: str | None = "<previous_rev>"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("website_url", sa.String(), nullable=False, server_default=""),
    )
    op.alter_column("tenants", "website_url", server_default=None)
    op.add_column(
        "tenants",
        sa.Column(
            "onboarding_status", sa.String(), nullable=False, server_default="PENDING"
        ),
    )


def downgrade() -> None:
    op.drop_column("tenants", "onboarding_status")
    op.drop_column("tenants", "website_url")
```

Run: `uv run alembic upgrade head`

- [ ] **Step 5: Update existing integration tests that use `TenantCreate`**

In `tests/integration/test_tenant_service.py`, add `website_url` to every `TenantCreate` call. Find all occurrences of `TenantCreate(` and add `website_url="https://example.com"`.

In `tests/integration/test_onboarding_endpoint.py`, update `_BUSINESS`:

```python
_BUSINESS = {
    "company_name": "Acme",
    "primary_contact_name": "Ada",
    "primary_contact_email": "ada@acme.com",
    "business_type": "B2B",
    "website_url": "https://acme.com",
    "timezone": "UTC",
    "language_preference": "en",
}
```

Also add `onboarding_status` assertion to `test_onboarding_creates_tenant_and_links_user`:

```python
    assert body["onboarding_status"] == "PENDING"
    assert body["website_url"] == "https://acme.com/"
```

- [ ] **Step 6: Run tests**

```bash
uv run pytest tests/unit/test_tenant_schemas.py tests/unit/test_tenant_model.py tests/integration/test_tenant_service.py tests/integration/test_onboarding_endpoint.py -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add shared/tenant/ tests/ migrations/
git commit -m "feat: add website_url and onboarding_status to tenants"
```

---

## Task 3: Add settings and implement `core/queue.py`

**Files:**
- Modify: `core/config.py`
- Modify: `core/queue.py`
- Modify: `core/lifespan.py`

- [ ] **Step 1: Add `anthropic_api_key` and `redis_url` to Settings**

In `core/config.py`, add two fields to `Settings`:

```python
    redis_url: str = "redis://localhost:6379"
    anthropic_api_key: str = ""
```

- [ ] **Step 2: Implement `core/queue.py`**

```python
"""ARQ Redis pool — the application's background job queue.

The pool is created once in lifespan and stored on app.state.arq_pool.
Endpoints that enqueue jobs inject it via get_arq_pool().
"""

from fastapi import Request
from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from core.config import get_settings


async def create_arq_pool() -> ArqRedis:
    """Create and return a connected ARQ Redis pool."""
    return await create_pool(RedisSettings.from_dsn(get_settings().redis_url))


def get_arq_pool(request: Request) -> ArqRedis:
    """FastAPI dependency: return the pool from app.state."""
    pool: ArqRedis = request.app.state.arq_pool
    return pool
```

- [ ] **Step 3: Update `core/lifespan.py` to init/close the pool**

```python
"""Application lifespan: startup and shutdown hooks for the FastAPI app."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from core.logging import configure_logging, get_logger
from core.queue import create_arq_pool


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    configure_logging()
    get_logger(__name__).info("application_startup")
    app.state.arq_pool = await create_arq_pool()
    yield
    await app.state.arq_pool.aclose()
    get_logger(__name__).info("application_shutdown")
```

- [ ] **Step 4: Run unit tests (no Redis needed)**

```bash
uv run pytest tests/unit/ -v
```

Expected: all pass (lifespan test may need updating — see Step 5).

- [ ] **Step 5: Fix lifespan unit test if needed**

Open `tests/unit/test_lifespan.py`. If it asserts on lifespan internals, add a mock for `create_arq_pool`. Check if it imports from `core.lifespan` and mock accordingly:

```python
# In the test, if the lifespan test creates a real app:
from unittest.mock import AsyncMock, patch

@pytest.fixture
def mock_arq(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_pool = AsyncMock()
    monkeypatch.setattr("core.lifespan.create_arq_pool", AsyncMock(return_value=mock_pool))
```

- [ ] **Step 6: Commit**

```bash
git add core/config.py core/queue.py core/lifespan.py tests/unit/test_lifespan.py
git commit -m "feat: implement core/queue.py ARQ pool and wire into lifespan"
```

---

## Task 4: Implement `clients/sonnet_client.py`

**Files:**
- Modify: `clients/sonnet_client.py`
- Create: `tests/unit/test_sonnet_client.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_sonnet_client.py`:

```python
"""Unit tests for clients/sonnet_client — mocks the Anthropic SDK."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clients.sonnet_client import call_with_tool
from core.exceptions import ExternalServiceError


def _make_tool_use_response(tool_name: str, data: dict[str, Any]) -> MagicMock:
    block = MagicMock()
    block.type = "tool_use"
    block.name = tool_name
    block.input = data
    response = MagicMock()
    response.content = [block]
    return response


async def test_call_with_tool_returns_tool_input(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = {"industry": "SaaS", "target_market": "SMB"}
    mock_create = AsyncMock(return_value=_make_tool_use_response("my_tool", expected))

    with patch("clients.sonnet_client.AsyncAnthropic") as mock_client_cls:
        mock_client_cls.return_value.messages.create = mock_create
        result = await call_with_tool(
            prompt="test prompt",
            tool_name="my_tool",
            tool_description="desc",
            input_schema={"type": "object", "properties": {}, "required": []},
        )

    assert result == expected


async def test_call_with_tool_raises_on_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    with patch("clients.sonnet_client.AsyncAnthropic") as mock_client_cls:
        mock_client_cls.return_value.messages.create = AsyncMock(
            side_effect=Exception("network error")
        )
        with pytest.raises(ExternalServiceError, match="Anthropic API call failed"):
            await call_with_tool(
                prompt="test",
                tool_name="tool",
                tool_description="desc",
                input_schema={"type": "object", "properties": {}, "required": []},
            )


async def test_call_with_tool_raises_when_no_tool_use_block() -> None:
    block = MagicMock()
    block.type = "text"
    response = MagicMock()
    response.content = [block]

    with patch("clients.sonnet_client.AsyncAnthropic") as mock_client_cls:
        mock_client_cls.return_value.messages.create = AsyncMock(return_value=response)
        with pytest.raises(ExternalServiceError, match="no tool_use block"):
            await call_with_tool(
                prompt="test",
                tool_name="tool",
                tool_description="desc",
                input_schema={"type": "object", "properties": {}, "required": []},
            )
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_sonnet_client.py -v
```

Expected: FAIL — `ImportError: cannot import name 'call_with_tool'`.

- [ ] **Step 3: Implement `clients/sonnet_client.py`**

```python
"""Thin async wrapper around the Anthropic Python SDK.

The only file in the project that imports `anthropic`. All LLM calls go
through `call_with_tool`, which forces structured output via tool use.
"""

from typing import Any

from anthropic import AsyncAnthropic

from core.config import get_settings
from core.exceptions import ExternalServiceError


async def call_with_tool(
    *,
    prompt: str,
    tool_name: str,
    tool_description: str,
    input_schema: dict[str, Any],
    model: str = "claude-sonnet-4-6",
    max_tokens: int = 2048,
) -> dict[str, Any]:
    """Call Anthropic with a single tool, forcing structured JSON output.

    Returns the tool_use block's input dict. Raises ExternalServiceError on
    any API failure or if the model returns no tool_use block.
    """
    client = AsyncAnthropic(api_key=get_settings().anthropic_api_key)
    try:
        response = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=0.0,
            tools=[
                {
                    "name": tool_name,
                    "description": tool_description,
                    "input_schema": input_schema,
                }
            ],
            tool_choice={"type": "tool", "name": tool_name},
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        raise ExternalServiceError(f"Anthropic API call failed: {exc}") from exc

    for block in response.content:
        if block.type == "tool_use" and block.name == tool_name:
            result: dict[str, Any] = block.input
            return result

    raise ExternalServiceError("Anthropic returned no tool_use block")
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_sonnet_client.py -v
```

Expected: all 3 pass.

- [ ] **Step 5: Commit**

```bash
git add clients/sonnet_client.py tests/unit/test_sonnet_client.py
git commit -m "feat: implement clients/sonnet_client call_with_tool"
```

---

## Task 5: Implement the three agents

**Files:**
- Create: `modules/tenant_onboarding/agents/__init__.py`
- Create: `modules/tenant_onboarding/agents/persona.py`
- Create: `modules/tenant_onboarding/agents/icp.py`
- Create: `modules/tenant_onboarding/agents/signals.py`
- Create: `tests/unit/test_onboarding_agents.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_onboarding_agents.py`:

```python
"""Unit tests for the three onboarding agents — Anthropic client is mocked."""

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from modules.tenant_onboarding.agents import icp, persona, signals
from shared.tenant.schemas import BusinessType
from shared.tenant_config.schemas import Dimension, Thresholds, Weights


def _patch_sonnet(return_value: dict[str, Any]) -> Any:
    return patch(
        "clients.sonnet_client.AsyncAnthropic",
        return_value=type(
            "C",
            (),
            {
                "messages": type(
                    "M",
                    (),
                    {
                        "create": AsyncMock(
                            return_value=type(
                                "R",
                                (),
                                {
                                    "content": [
                                        type(
                                            "B",
                                            (),
                                            {
                                                "type": "tool_use",
                                                "name": list(return_value.keys())[0]
                                                if False
                                                else "stub",
                                                "input": return_value,
                                            },
                                        )()
                                    ]
                                },
                            )()
                        )
                    },
                )()
            },
        )(),
    )


# ── Simpler approach: patch call_with_tool directly ──


async def test_persona_agent_returns_business_profile() -> None:
    expected = {
        "industry": "SaaS",
        "target_market": "SMB",
        "products_services": "CRM",
        "company_size": "startup",
        "geography": "Global",
        "value_proposition": "Saves time",
    }
    with patch(
        "modules.tenant_onboarding.agents.persona.call_with_tool",
        new=AsyncMock(return_value=expected),
    ):
        result = await persona.run(
            company_name="Acme",
            business_type=BusinessType.B2B,
            website_text="We build CRM software for small businesses.",
        )
    assert result == expected


async def test_icp_agent_returns_icp() -> None:
    business_profile = {"industry": "SaaS", "target_market": "SMB"}
    expected = {
        "buyer_role": "VP Sales",
        "company_size": "50-200",
        "industry_vertical": "Tech",
        "pain_points": "Manual tracking",
        "budget_range": "$10k-50k",
        "decision_timeline": "3 months",
    }
    with patch(
        "modules.tenant_onboarding.agents.icp.call_with_tool",
        new=AsyncMock(return_value=expected),
    ):
        result = await icp.run(business_profile)
    assert result == expected


async def test_signals_agent_returns_signals_weights_thresholds() -> None:
    raw = {
        "signals": [
            {"id": "fit_1", "dimension": "FIT", "question": "Right industry?"},
            {"id": "intent_1", "dimension": "INTENT", "question": "Visited pricing?"},
            {"id": "eng_1", "dimension": "ENGAGEMENT", "question": "Opened email?"},
            {"id": "beh_1", "dimension": "BEHAVIOUR", "question": "Requested demo?"},
            {"id": "ctx_1", "dimension": "CONTEXT", "question": "Raised funding?"},
        ],
        "weights": {
            "fit": 0.2,
            "intent": 0.2,
            "engagement": 0.2,
            "behaviour": 0.2,
            "context": 0.2,
        },
        "thresholds": {"hot": 80, "warm": 55},
    }
    with patch(
        "modules.tenant_onboarding.agents.signals.call_with_tool",
        new=AsyncMock(return_value=raw),
    ):
        sigs, weights, thresholds = await signals.run(
            business_profile={"industry": "SaaS"},
            icp={"buyer_role": "VP Sales"},
        )
    assert len(sigs) == 5
    assert all(s.dimension in Dimension for s in sigs)
    assert isinstance(weights, Weights)
    assert isinstance(thresholds, Thresholds)
    assert thresholds.hot == 80
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_onboarding_agents.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'modules.tenant_onboarding.agents.persona'`.

- [ ] **Step 3: Create `modules/tenant_onboarding/agents/__init__.py`**

```python
```
(empty)

- [ ] **Step 4: Implement `modules/tenant_onboarding/agents/persona.py`**

```python
"""Persona agent — derives a structured business profile from website content."""

from typing import Any

from clients.sonnet_client import call_with_tool
from shared.tenant.schemas import BusinessType

_TOOL_NAME = "output_business_profile"
_TOOL_DESCRIPTION = "Output the structured business profile extracted from the website."
_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "industry": {"type": "string"},
        "target_market": {"type": "string"},
        "products_services": {"type": "string"},
        "company_size": {"type": "string"},
        "geography": {"type": "string"},
        "value_proposition": {"type": "string"},
    },
    "required": [
        "industry",
        "target_market",
        "products_services",
        "company_size",
        "geography",
        "value_proposition",
    ],
}


async def run(
    company_name: str,
    business_type: BusinessType,
    website_text: str,
) -> dict[str, Any]:
    """Return a business_profile dict derived from the website content."""
    prompt = (
        f"You are analyzing a business to build its profile.\n\n"
        f"Company name: {company_name}\n"
        f"Business type: {business_type.value}\n\n"
        f"Website content:\n{website_text[:8000]}\n\n"
        f"Extract a structured business profile based only on the information above. "
        f"Be factual and concise."
    )
    return await call_with_tool(
        prompt=prompt,
        tool_name=_TOOL_NAME,
        tool_description=_TOOL_DESCRIPTION,
        input_schema=_INPUT_SCHEMA,
    )
```

- [ ] **Step 5: Implement `modules/tenant_onboarding/agents/icp.py`**

```python
"""ICP agent — derives the ideal customer profile from a business profile."""

import json
from typing import Any

from clients.sonnet_client import call_with_tool

_TOOL_NAME = "output_icp"
_TOOL_DESCRIPTION = "Output the structured ideal customer profile."
_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "buyer_role": {"type": "string"},
        "company_size": {"type": "string"},
        "industry_vertical": {"type": "string"},
        "pain_points": {"type": "string"},
        "budget_range": {"type": "string"},
        "decision_timeline": {"type": "string"},
    },
    "required": [
        "buyer_role",
        "company_size",
        "industry_vertical",
        "pain_points",
        "budget_range",
        "decision_timeline",
    ],
}


async def run(business_profile: dict[str, Any]) -> dict[str, Any]:
    """Return an icp dict derived from the business profile."""
    prompt = (
        f"You are building an ideal customer profile (ICP) for a business.\n\n"
        f"Business profile:\n{json.dumps(business_profile, indent=2)}\n\n"
        f"Based on this business profile, define who their ideal customer is. "
        f"Be specific and actionable."
    )
    return await call_with_tool(
        prompt=prompt,
        tool_name=_TOOL_NAME,
        tool_description=_TOOL_DESCRIPTION,
        input_schema=_INPUT_SCHEMA,
    )
```

- [ ] **Step 6: Implement `modules/tenant_onboarding/agents/signals.py`**

```python
"""Signals agent — generates scoring signals, weights, and thresholds."""

import json
from typing import Any

from clients.sonnet_client import call_with_tool
from shared.tenant_config.schemas import Signal, Thresholds, Weights

_TOOL_NAME = "output_scoring_config"
_TOOL_DESCRIPTION = "Output the signals, weights, and thresholds for lead scoring."
_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "signals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "dimension": {
                        "type": "string",
                        "enum": ["FIT", "INTENT", "ENGAGEMENT", "BEHAVIOUR", "CONTEXT"],
                    },
                    "question": {"type": "string"},
                },
                "required": ["id", "dimension", "question"],
            },
        },
        "weights": {
            "type": "object",
            "properties": {
                "fit": {"type": "number"},
                "intent": {"type": "number"},
                "engagement": {"type": "number"},
                "behaviour": {"type": "number"},
                "context": {"type": "number"},
            },
            "required": ["fit", "intent", "engagement", "behaviour", "context"],
        },
        "thresholds": {
            "type": "object",
            "properties": {
                "hot": {"type": "integer"},
                "warm": {"type": "integer"},
            },
            "required": ["hot", "warm"],
        },
    },
    "required": ["signals", "weights", "thresholds"],
}


async def run(
    business_profile: dict[str, Any],
    icp: dict[str, Any],
) -> tuple[list[Signal], Weights, Thresholds]:
    """Return (signals, weights, thresholds) ready for TenantConfigCreate."""
    prompt = (
        f"You are building a lead scoring configuration.\n\n"
        f"Business profile:\n{json.dumps(business_profile, indent=2)}\n\n"
        f"Ideal customer profile:\n{json.dumps(icp, indent=2)}\n\n"
        f"Generate:\n"
        f"1. Signals: yes/no questions identifying whether a lead matches this ICP. "
        f"Cover all five dimensions: FIT, INTENT, ENGAGEMENT, BEHAVIOUR, CONTEXT. "
        f"At least one signal per dimension. Use unique snake_case IDs.\n"
        f"2. Weights: how much each dimension matters (must sum to 1.0).\n"
        f"3. Thresholds: score cutoffs. Default: hot=80, warm=55."
    )
    result = await call_with_tool(
        prompt=prompt,
        tool_name=_TOOL_NAME,
        tool_description=_TOOL_DESCRIPTION,
        input_schema=_INPUT_SCHEMA,
    )
    sigs = [Signal.model_validate(s) for s in result["signals"]]
    weights = Weights.model_validate(result["weights"])
    thresholds = Thresholds.model_validate(result["thresholds"])
    return sigs, weights, thresholds
```

- [ ] **Step 7: Run tests**

```bash
uv run pytest tests/unit/test_onboarding_agents.py -v
```

Expected: all 3 pass.

- [ ] **Step 8: Commit**

```bash
git add modules/tenant_onboarding/agents/ tests/unit/test_onboarding_agents.py
git commit -m "feat: add persona, icp, and signals agents"
```

---

## Task 6: Implement the pipeline

**Files:**
- Create: `modules/tenant_onboarding/pipeline.py`
- Create: `tests/unit/test_onboarding_pipeline.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_onboarding_pipeline.py`:

```python
"""Unit tests for modules/tenant_onboarding/pipeline — all I/O is mocked."""

import re
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from modules.tenant_onboarding.pipeline import _strip_html, run_pipeline
from shared.tenant.schemas import OnboardingStatus


def _mock_tenant(tenant_id: "UUID | None" = None) -> MagicMock:
    from uuid import uuid4 as _uuid4
    from shared.tenant.schemas import BusinessType

    t = MagicMock()
    t.id = tenant_id or _uuid4()
    t.company_name = "Acme"
    t.business_type = BusinessType.B2B
    t.website_url = "https://acme.com"
    return t


def test_strip_html_removes_tags() -> None:
    html = "<html><body><h1>Hello</h1><script>bad()</script><p>World</p></body></html>"
    result = _strip_html(html)
    assert "Hello" in result
    assert "World" in result
    assert "<" not in result
    assert "bad()" not in result


async def test_pipeline_happy_path_sets_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    from uuid import UUID

    tenant_id = uuid4()
    mock_session = AsyncMock()

    business_profile = {"industry": "SaaS", "target_market": "SMB"}
    icp_data = {"buyer_role": "VP Sales", "company_size": "50-200"}
    from shared.tenant_config.schemas import Dimension, Signal, Thresholds, Weights

    sigs = [
        Signal(id=f"{d.value.lower()}_1", dimension=d, question="?")
        for d in Dimension
    ]
    weights = Weights(fit=0.2, intent=0.2, engagement=0.2, behaviour=0.2, context=0.2)
    thresholds = Thresholds(hot=80, warm=55)

    monkeypatch.setattr(
        "modules.tenant_onboarding.pipeline.get_tenant",
        AsyncMock(return_value=_mock_tenant(tenant_id)),
    )
    monkeypatch.setattr(
        "modules.tenant_onboarding.pipeline.set_onboarding_status", AsyncMock()
    )
    monkeypatch.setattr(
        "modules.tenant_onboarding.pipeline.persona.run",
        AsyncMock(return_value=business_profile),
    )
    monkeypatch.setattr(
        "modules.tenant_onboarding.pipeline.icp.run", AsyncMock(return_value=icp_data)
    )
    monkeypatch.setattr(
        "modules.tenant_onboarding.pipeline.signals.run",
        AsyncMock(return_value=(sigs, weights, thresholds)),
    )
    monkeypatch.setattr(
        "modules.tenant_onboarding.pipeline.create_active", AsyncMock()
    )
    monkeypatch.setattr(
        "modules.tenant_onboarding.pipeline.activate_tenant", AsyncMock()
    )

    mock_http_response = MagicMock()
    mock_http_response.text = "<html><body>Acme sells CRM</body></html>"
    mock_http_response.raise_for_status = MagicMock()

    with patch("modules.tenant_onboarding.pipeline.httpx.AsyncClient") as mock_http:
        mock_http.return_value.__aenter__.return_value.get = AsyncMock(
            return_value=mock_http_response
        )
        await run_pipeline(mock_session, tenant_id)

    from modules.tenant_onboarding.pipeline import set_onboarding_status as sos

    calls = [c.args[2] for c in sos.call_args_list]  # type: ignore[attr-defined]
    assert OnboardingStatus.RUNNING in calls
    assert OnboardingStatus.COMPLETE in calls


async def test_pipeline_sets_failed_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    from uuid import uuid4 as _uuid4

    tenant_id = _uuid4()
    mock_session = AsyncMock()

    monkeypatch.setattr(
        "modules.tenant_onboarding.pipeline.get_tenant",
        AsyncMock(return_value=_mock_tenant(tenant_id)),
    )
    set_status_mock = AsyncMock()
    monkeypatch.setattr(
        "modules.tenant_onboarding.pipeline.set_onboarding_status", set_status_mock
    )
    monkeypatch.setattr(
        "modules.tenant_onboarding.pipeline.persona.run",
        AsyncMock(side_effect=Exception("network timeout")),
    )

    with patch("modules.tenant_onboarding.pipeline.httpx.AsyncClient") as mock_http:
        mock_http_response = MagicMock()
        mock_http_response.text = "<html>content</html>"
        mock_http_response.raise_for_status = MagicMock()
        mock_http.return_value.__aenter__.return_value.get = AsyncMock(
            return_value=mock_http_response
        )
        with pytest.raises(Exception, match="network timeout"):
            await run_pipeline(mock_session, tenant_id)

    calls = [c.args[2] for c in set_status_mock.call_args_list]
    assert OnboardingStatus.FAILED in calls
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_onboarding_pipeline.py -v
```

Expected: FAIL — `ImportError: cannot import name 'run_pipeline'`.

- [ ] **Step 3: Implement `modules/tenant_onboarding/pipeline.py`**

```python
"""Pipeline: fetches the tenant's website and runs the three agents in sequence."""

import re
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from modules.tenant_onboarding.agents import icp, persona, signals
from shared.tenant.schemas import OnboardingStatus
from shared.tenant.service import activate_tenant, get_tenant, set_onboarding_status
from shared.tenant_config.schemas import TenantConfigCreate
from shared.tenant_config.service import create_active


async def run_pipeline(session: AsyncSession, tenant_id: UUID) -> None:
    """Run the full onboarding pipeline: website fetch → 3 agents → activate."""
    await set_onboarding_status(session, tenant_id, OnboardingStatus.RUNNING)
    try:
        tenant = await get_tenant(session, tenant_id)

        async with httpx.AsyncClient(timeout=30.0) as http:
            response = await http.get(str(tenant.website_url))
            response.raise_for_status()
        website_text = _strip_html(response.text)

        business_profile = await persona.run(
            company_name=tenant.company_name,
            business_type=tenant.business_type,
            website_text=website_text,
        )
        icp_data = await icp.run(business_profile)
        sigs, weights, thresholds = await signals.run(business_profile, icp_data)

        config_data = TenantConfigCreate(
            business_profile=business_profile,
            icp=icp_data,
            signals=sigs,
            weights=weights,
            thresholds=thresholds,
        )
        await create_active(session, tenant_id, config_data)
        await activate_tenant(session, tenant_id)
        await set_onboarding_status(session, tenant_id, OnboardingStatus.COMPLETE)

    except Exception:
        await set_onboarding_status(session, tenant_id, OnboardingStatus.FAILED)
        raise


def _strip_html(html: str) -> str:
    """Extract readable text from HTML without external dependencies."""
    text = re.sub(
        r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE
    )
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_onboarding_pipeline.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add modules/tenant_onboarding/pipeline.py tests/unit/test_onboarding_pipeline.py
git commit -m "feat: implement tenant onboarding pipeline"
```

---

## Task 7: Wire workers and update `POST /onboarding`

**Files:**
- Modify: `workers/worker.py`
- Create: `workers/jobs/onboarding.py`
- Modify: `api/onboarding.py`
- Modify: `tests/integration/test_onboarding_endpoint.py`

- [ ] **Step 1: Implement `workers/jobs/onboarding.py`**

```python
"""ARQ job: run the full onboarding pipeline for a tenant."""

from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.config import get_settings
from modules.tenant_onboarding.pipeline import run_pipeline


async def run_onboarding_pipeline(ctx: dict[str, object], tenant_id: str) -> None:
    """Fetch the tenant's website and run persona → ICP → signals pipeline."""
    engine = create_async_engine(get_settings().database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await run_pipeline(session, UUID(tenant_id))
    await engine.dispose()
```

- [ ] **Step 2: Implement `workers/worker.py`**

```python
"""ARQ worker entry point — registers all background jobs."""

from arq.connections import RedisSettings

from core.config import get_settings
from workers.jobs.onboarding import run_onboarding_pipeline


class WorkerSettings:
    """ARQ worker configuration. Run with: uv run arq workers.worker.WorkerSettings"""

    functions = [run_onboarding_pipeline]

    @classmethod
    def redis_settings(cls) -> RedisSettings:
        return RedisSettings.from_dsn(get_settings().redis_url)
```

- [ ] **Step 3: Update `api/onboarding.py` to enqueue the job**

```python
"""The /onboarding endpoint — submits business info, creates tenant, starts pipeline."""

from typing import Annotated

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import get_current_user
from auth.models import User
from auth.service import set_user_tenant
from core.db import get_session
from core.exceptions import ConflictError
from core.queue import get_arq_pool
from shared.tenant.schemas import TenantCreate, TenantRead
from shared.tenant.service import create_tenant

router = APIRouter()


@router.post("/onboarding", response_model=TenantRead)
async def onboard(
    data: TenantCreate,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    arq_pool: Annotated[ArqRedis, Depends(get_arq_pool)],
) -> TenantRead:
    """Create the current user's tenant from their business info, then start the pipeline."""
    if user.tenant_id is not None:
        raise ConflictError("User is already onboarded to a tenant")
    tenant = await create_tenant(session, data)
    await set_user_tenant(session, user, tenant.id)
    await arq_pool.enqueue_job("run_onboarding_pipeline", tenant_id=str(tenant.id))
    return TenantRead.model_validate(tenant)
```

- [ ] **Step 4: Update integration tests to mock the ARQ pool**

In `tests/integration/test_onboarding_endpoint.py`, import `get_arq_pool` and add a pool mock to the `client` fixture:

```python
from unittest.mock import AsyncMock

from core.queue import get_arq_pool

# Inside the client fixture, before transport setup, add:
mock_pool = AsyncMock()
mock_pool.enqueue_job = AsyncMock()
app.dependency_overrides[get_arq_pool] = lambda: mock_pool
```

Full updated fixture:

```python
@pytest.fixture
async def client(
    monkeypatch: pytest.MonkeyPatch, session: AsyncSession
) -> AsyncIterator[AsyncClient]:
    def fake_verify(token: str) -> dict[str, Any]:
        if token == "tenant-token":
            return {"sub": "auth0|newtenant", f"{NS}email": "ada@acme.com", f"{NS}role": "TENANT"}
        from core.exceptions import AuthenticationError
        raise AuthenticationError("bad token")

    monkeypatch.setattr(token_module, "verify_token", fake_verify)

    async def _use_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    mock_pool = AsyncMock()
    mock_pool.enqueue_job = AsyncMock()

    app.dependency_overrides[get_session] = _use_test_session
    app.dependency_overrides[get_arq_pool] = lambda: mock_pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
```

Also add a test to verify the job is enqueued:

```python
async def test_onboarding_enqueues_pipeline_job(
    client: AsyncClient,
) -> None:
    from core.queue import get_arq_pool as _get_arq_pool
    mock_pool = app.dependency_overrides[_get_arq_pool]()
    resp = await client.post("/onboarding", headers=_auth(), json=_BUSINESS)
    assert resp.status_code == 200
    mock_pool.enqueue_job.assert_awaited_once()
    call_kwargs = mock_pool.enqueue_job.call_args
    assert call_kwargs.args[0] == "run_onboarding_pipeline"
```

- [ ] **Step 5: Run integration tests**

```bash
uv run pytest tests/integration/test_onboarding_endpoint.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add workers/ api/onboarding.py tests/integration/test_onboarding_endpoint.py
git commit -m "feat: wire ARQ worker and enqueue pipeline from POST /onboarding"
```

---

## Task 8: Full CI gate

**Files:** none (verification only)

- [ ] **Step 1: Run full CI**

```bash
make ci
```

Expected: `ruff check .` clean, `mypy .` (strict) clean, full pytest suite green.

- [ ] **Step 2: Fix any issues**

Likely issues and fixes:
- **`mypy` on `pipeline.py`**: `tenant.website_url` is `str` (not `AnyHttpUrl`) so `str(tenant.website_url)` is fine. If mypy complains about `block.input` type in `sonnet_client.py`, add `# type: ignore[return-value]` on that line.
- **`mypy` on `worker.py`**: `redis_settings` should be a `classmethod`, not a `property`, for ARQ compatibility. If mypy flags `WorkerSettings.redis_settings`, change to `redis_settings = RedisSettings.from_dsn(get_settings().redis_url)` as a class attribute.
- **`ruff` import order**: run `make format` first.
- **`test_onboarding_pipeline.py` type errors**: the `UUID` import inside the function body — move to top of file.

- [ ] **Step 3: Commit any fixes**

```bash
git add -A
git commit -m "chore: fix lint and type errors for onboarding pipeline"
```

(Skip if Step 1 passed clean.)

---

## Self-Review

**Spec coverage:**
- ✅ `ConfigStatus` simplified to ACTIVE/ARCHIVED
- ✅ `website_url` + `onboarding_status` on tenants
- ✅ `OnboardingStatus` in `shared/tenant/schemas.py` (correct location for boundary rule)
- ✅ `create_active` archives previous ACTIVE and writes new one
- ✅ `set_onboarding_status` in `shared/tenant/service.py`
- ✅ `clients/sonnet_client.py` with `call_with_tool`
- ✅ All three agents with unit tests
- ✅ Pipeline orchestrates fetch → persona → ICP → signals → create_active → activate_tenant → COMPLETE
- ✅ `onboarding_status = FAILED` on any exception
- ✅ `core/queue.py` implemented
- ✅ ARQ worker registered
- ✅ `POST /onboarding` enqueues job
- ✅ `GET /me` reflects `onboarding_status` via `TenantRead` (no endpoint change needed)
- ✅ Existing integration tests updated for `website_url`
- ✅ ARQ pool mocked in integration tests
- ✅ `TenantActivated` event deferred (no bus yet, per ADR 0001) — not in plan, correctly omitted

**Deferred (per spec):** `TenantActivated` delivery, retry endpoint, HTML size cap.
