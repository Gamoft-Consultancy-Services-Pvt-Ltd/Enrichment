# shared/tenant_config Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `shared/tenant_config` — a versioned registry of per-tenant scoring configuration (schemas, ORM model, migration, public service), and drop the obsolete `shared/prompt_registry` stub.

**Architecture:** Follows the `shared/tenant` precedent exactly: a public `schemas.py` (enums + pydantic value objects), an internal `models.py` (one ORM table on `core.db.Base`), and a public `service.py` (the only read/write path, raising `core.exceptions` errors). The table is versioned with a `DRAFT → ACTIVE → ARCHIVED` / `DRAFT → REJECTED` lifecycle, enforced by two Postgres partial unique indexes (one `ACTIVE`, one `DRAFT` per tenant). Validation of the `JSONB` payloads lives entirely in pydantic.

**Tech Stack:** Python 3.13, SQLAlchemy 2.0 (async, `asyncpg`), Postgres `JSONB`, Alembic, pydantic v2, pytest (`asyncio_mode = auto`), real Postgres for integration tests.

**Spec:** `docs/superpowers/specs/2026-06-07-shared-tenant-config-design.md`

> **Environment note:** Integration tasks need a running Postgres (`DATABASE_URL`). Start the stack first: `cd /home/harsh/Enrichment-github && docker compose up -d`. Run commands inside the app container or with the project venv as the codebase already does (`uv run …`). The integration conftest applies migrations automatically at session start.

---

## File Structure

- `shared/tenant_config/schemas.py` — **create**: `ConfigStatus`, `Dimension` enums; `Signal`, `Weights`, `Thresholds`, `TenantConfigCreate`, `TenantConfigRead` pydantic models with validators. The entire public surface for types.
- `shared/tenant_config/models.py` — **create**: the `TenantConfig` ORM model + partial unique indexes. Internal; nobody imports it but `service.py`, `migrations/env.py`, and `tests/integration/conftest.py`.
- `shared/tenant_config/service.py` — **create**: `get_active_config`, `create_draft`, `approve_version`, `reject_version`, `list_versions`. The only read/write path.
- `shared/tenant_config/__init__.py` — **leave empty** (already exists; consistent with `shared/tenant`).
- `migrations/env.py` — **modify**: import `shared.tenant_config.models` so autogenerate/metadata sees the table.
- `migrations/versions/<rev>_create_tenant_configs_table.py` — **create** (via `alembic revision`): the table + indexes.
- `tests/integration/conftest.py` — **modify**: import `shared.tenant_config.models` so `Base.metadata` is complete for truncation.
- `tests/unit/test_tenant_config_schemas.py` — **create**: pydantic validation tests (no DB).
- `tests/integration/test_tenant_config_service.py` — **create**: service tests against real Postgres.
- `shared/prompt_registry/` — **delete**: obsolete stub (merged into `tenant_config`).
- `CLAUDE.md` — **modify**: drop `prompt_registry` from the `shared/` layer list and pipeline narrative.

---

## Task 1: Status and Dimension enums

**Files:**
- Create: `shared/tenant_config/schemas.py`
- Test: `tests/unit/test_tenant_config_schemas.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_tenant_config_schemas.py`:

```python
"""Unit tests for shared.tenant_config.schemas — pure validation, no DB."""

from shared.tenant_config.schemas import ConfigStatus, Dimension


def test_config_status_membership_is_exact() -> None:
    assert {m.value for m in ConfigStatus} == {"DRAFT", "ACTIVE", "ARCHIVED", "REJECTED"}


def test_dimension_membership_is_exact() -> None:
    assert {m.value for m in Dimension} == {
        "FIT",
        "INTENT",
        "ENGAGEMENT",
        "BEHAVIOUR",
        "CONTEXT",
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_tenant_config_schemas.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shared.tenant_config.schemas'`.

- [ ] **Step 3: Write minimal implementation**

Create `shared/tenant_config/schemas.py`:

```python
"""Public schemas and enums for the tenant_config registry.

The single source of truth for the config status/dimension enums and the
validated value objects (signals, weights, thresholds). models.py, service.py,
modules/scoring, and tests import from here — never from models.py.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ConfigStatus(StrEnum):
    """The lifecycle state of a single tenant_config version."""

    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"
    REJECTED = "REJECTED"


class Dimension(StrEnum):
    """The five scoring dimensions every config must cover."""

    FIT = "FIT"
    INTENT = "INTENT"
    ENGAGEMENT = "ENGAGEMENT"
    BEHAVIOUR = "BEHAVIOUR"
    CONTEXT = "CONTEXT"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_tenant_config_schemas.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add shared/tenant_config/schemas.py tests/unit/test_tenant_config_schemas.py
git commit -m "feat: add ConfigStatus and Dimension enums for tenant_config"
```

---

## Task 2: Value objects — Signal, Weights, Thresholds

**Files:**
- Modify: `shared/tenant_config/schemas.py`
- Test: `tests/unit/test_tenant_config_schemas.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_tenant_config_schemas.py` (add the imports to the existing import line):

```python
import pytest
from pydantic import ValidationError

from shared.tenant_config.schemas import Signal, Thresholds, Weights


def _balanced_weights() -> dict[str, float]:
    return {"fit": 0.2, "intent": 0.2, "engagement": 0.2, "behaviour": 0.2, "context": 0.2}


def test_weights_accepts_values_summing_to_one() -> None:
    w = Weights(**_balanced_weights())
    assert w.fit == 0.2


def test_weights_accepts_sum_within_float_tolerance() -> None:
    # 0.1 * 3 + 0.7 is not exactly 1.0 in float, but within tolerance.
    Weights(fit=0.1, intent=0.1, engagement=0.1, behaviour=0.0, context=0.7)


def test_weights_rejects_sum_not_one() -> None:
    bad = _balanced_weights() | {"context": 0.5}
    with pytest.raises(ValidationError):
        Weights(**bad)


def test_weights_rejects_out_of_range_value() -> None:
    bad = _balanced_weights() | {"fit": 1.5, "intent": -0.3}
    with pytest.raises(ValidationError):
        Weights(**bad)


def test_weights_rejects_missing_dimension() -> None:
    bad = _balanced_weights()
    del bad["context"]
    with pytest.raises(ValidationError):
        Weights(**bad)


def test_thresholds_accepts_hot_above_warm() -> None:
    t = Thresholds(hot=80, warm=55)
    assert t.hot == 80


def test_thresholds_rejects_hot_equal_to_warm() -> None:
    with pytest.raises(ValidationError):
        Thresholds(hot=55, warm=55)


def test_thresholds_rejects_hot_below_warm() -> None:
    with pytest.raises(ValidationError):
        Thresholds(hot=40, warm=55)


def test_thresholds_rejects_out_of_range() -> None:
    with pytest.raises(ValidationError):
        Thresholds(hot=120, warm=55)


def test_signal_requires_valid_dimension() -> None:
    with pytest.raises(ValidationError):
        Signal.model_validate({"id": "s1", "dimension": "NOPE", "question": "?"})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_tenant_config_schemas.py -v`
Expected: FAIL — `ImportError: cannot import name 'Signal' from 'shared.tenant_config.schemas'`.

- [ ] **Step 3: Write minimal implementation**

Append to `shared/tenant_config/schemas.py`:

```python
class Signal(BaseModel):
    """A single yes/no question tied to one scoring dimension."""

    id: str
    dimension: Dimension
    question: str


class Weights(BaseModel):
    """Per-dimension weights: all five present, each in [0, 1], summing to 1.0."""

    fit: float = Field(ge=0.0, le=1.0)
    intent: float = Field(ge=0.0, le=1.0)
    engagement: float = Field(ge=0.0, le=1.0)
    behaviour: float = Field(ge=0.0, le=1.0)
    context: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _sum_to_one(self) -> Self:
        total = self.fit + self.intent + self.engagement + self.behaviour + self.context
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"weights must sum to 1.0 (got {total})")
        return self


class Thresholds(BaseModel):
    """Score cutoffs for bucketing; hot must be strictly greater than warm."""

    hot: int = Field(ge=0, le=100)
    warm: int = Field(ge=0, le=100)

    @model_validator(mode="after")
    def _hot_above_warm(self) -> Self:
        if self.hot <= self.warm:
            raise ValueError(f"hot ({self.hot}) must be greater than warm ({self.warm})")
        return self
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_tenant_config_schemas.py -v`
Expected: PASS (all Task 1 + Task 2 tests).

- [ ] **Step 5: Commit**

```bash
git add shared/tenant_config/schemas.py tests/unit/test_tenant_config_schemas.py
git commit -m "feat: add Signal, Weights, Thresholds value objects with validators"
```

---

## Task 3: TenantConfigCreate and TenantConfigRead

**Files:**
- Modify: `shared/tenant_config/schemas.py`
- Test: `tests/unit/test_tenant_config_schemas.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_tenant_config_schemas.py` (add imports for the new names + the stdlib helpers used):

```python
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from shared.tenant_config.schemas import (
    ConfigStatus,
    TenantConfigCreate,
    TenantConfigRead,
)


def _all_dimension_signals() -> list[dict[str, str]]:
    return [
        {"id": "fit_1", "dimension": "FIT", "question": "In target industry?"},
        {"id": "intent_1", "dimension": "INTENT", "question": "Visited pricing?"},
        {"id": "eng_1", "dimension": "ENGAGEMENT", "question": "Opened last email?"},
        {"id": "beh_1", "dimension": "BEHAVIOUR", "question": "Requested a demo?"},
        {"id": "ctx_1", "dimension": "CONTEXT", "question": "Raised funding recently?"},
    ]


def _valid_create_payload() -> dict[str, object]:
    return {
        "business_profile": {"summary": "B2B SaaS"},
        "icp": {"summary": "Mid-market SaaS in APAC"},
        "signals": _all_dimension_signals(),
        "weights": _balanced_weights(),
        "thresholds": {"hot": 80, "warm": 55},
    }


def test_tenant_config_create_accepts_valid_payload() -> None:
    data = TenantConfigCreate.model_validate(_valid_create_payload())
    assert len(data.signals) == 5
    assert data.weights.fit == 0.2


def test_tenant_config_create_rejects_empty_signals() -> None:
    payload = _valid_create_payload() | {"signals": []}
    with pytest.raises(ValidationError):
        TenantConfigCreate.model_validate(payload)


def test_tenant_config_create_rejects_duplicate_signal_ids() -> None:
    signals = _all_dimension_signals()
    signals[1]["id"] = signals[0]["id"]  # duplicate id
    payload = _valid_create_payload() | {"signals": signals}
    with pytest.raises(ValidationError):
        TenantConfigCreate.model_validate(payload)


def test_tenant_config_create_rejects_missing_dimension() -> None:
    signals = _all_dimension_signals()[:-1]  # drop the CONTEXT signal
    payload = _valid_create_payload() | {"signals": signals}
    with pytest.raises(ValidationError):
        TenantConfigCreate.model_validate(payload)


def test_tenant_config_read_builds_from_orm_like_object() -> None:
    obj = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        version=1,
        status=ConfigStatus.ACTIVE,
        business_profile={"summary": "B2B SaaS"},
        icp={"summary": "Mid-market SaaS"},
        signals=_all_dimension_signals(),
        weights=_balanced_weights(),
        thresholds={"hot": 80, "warm": 55},
        created_at=datetime.now(UTC),
        activated_at=datetime.now(UTC),
        archived_at=None,
    )
    read = TenantConfigRead.model_validate(obj)
    assert read.version == 1
    assert read.status is ConfigStatus.ACTIVE
    # nested JSONB payloads are coerced back into typed value objects on read
    assert read.weights.fit == 0.2
    assert read.signals[0].dimension.value == "FIT"
    assert read.archived_at is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_tenant_config_schemas.py -v`
Expected: FAIL — `ImportError: cannot import name 'TenantConfigCreate'`.

- [ ] **Step 3: Write minimal implementation**

Append to `shared/tenant_config/schemas.py`:

```python
class TenantConfigCreate(BaseModel):
    """The draft payload the onboarding agent chain produces for a new version."""

    business_profile: dict[str, Any]
    icp: dict[str, Any]
    signals: list[Signal]
    weights: Weights
    thresholds: Thresholds

    @field_validator("signals")
    @classmethod
    def _cover_all_dimensions_with_unique_ids(cls, value: list[Signal]) -> list[Signal]:
        if not value:
            raise ValueError("signals must not be empty")
        ids = [s.id for s in value]
        if len(ids) != len(set(ids)):
            raise ValueError("signal ids must be unique within a version")
        missing = set(Dimension) - {s.dimension for s in value}
        if missing:
            names = ", ".join(sorted(d.value for d in missing))
            raise ValueError(f"every dimension needs at least one signal; missing: {names}")
        return value


class TenantConfigRead(BaseModel):
    """The full config record returned to callers; built from the ORM object.

    The JSONB columns come back as plain dicts/lists; pydantic re-validates them
    into the typed value objects, so a hand-edited bad row is caught on read.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    version: int
    status: ConfigStatus
    business_profile: dict[str, Any]
    icp: dict[str, Any]
    signals: list[Signal]
    weights: Weights
    thresholds: Thresholds
    created_at: datetime
    activated_at: datetime | None
    archived_at: datetime | None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_tenant_config_schemas.py -v`
Expected: PASS (all schema tests).

- [ ] **Step 5: Typecheck and lint the new file**

Run: `uv run mypy shared/tenant_config/schemas.py tests/unit/test_tenant_config_schemas.py && uv run ruff check shared/tenant_config/schemas.py tests/unit/test_tenant_config_schemas.py`
Expected: PASS. If ruff flags import ordering, run `uv run ruff check --fix .` and re-run.

- [ ] **Step 6: Commit**

```bash
git add shared/tenant_config/schemas.py tests/unit/test_tenant_config_schemas.py
git commit -m "feat: add TenantConfigCreate/TenantConfigRead schemas with cross-field validation"
```

---

## Task 4: ORM model, migration, and wiring

**Files:**
- Create: `shared/tenant_config/models.py`
- Modify: `migrations/env.py`
- Modify: `tests/integration/conftest.py`
- Create: `migrations/versions/<rev>_create_tenant_configs_table.py` (generated)

- [ ] **Step 1: Write the ORM model**

Create `shared/tenant_config/models.py`:

```python
"""The TenantConfig ORM model (internal to the tenant_config module).

Other modules never import this; they use shared.tenant_config.service and
shared.tenant_config.schemas. The status enum is defined in schemas.py and
reused here. Two partial unique indexes enforce at most one ACTIVE and one
DRAFT version per tenant.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, UniqueConstraint, Uuid, func, text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.db import Base
from shared.tenant_config.schemas import ConfigStatus


class TenantConfig(Base):
    """A versioned scoring-configuration record owned by one tenant."""

    __tablename__ = "tenant_configs"

    __table_args__ = (
        UniqueConstraint("tenant_id", "version", name="uq_tenant_config_version"),
        Index(
            "uq_active_config_per_tenant",
            "tenant_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
        Index(
            "uq_draft_config_per_tenant",
            "tenant_id",
            unique=True,
            postgresql_where=text("status = 'DRAFT'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ConfigStatus] = mapped_column(
        SQLEnum(ConfigStatus, name="config_status"), nullable=False
    )
    business_profile: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    icp: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    signals: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    weights: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    thresholds: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

- [ ] **Step 2: Register the model for migrations and tests**

In `migrations/env.py`, add the import alongside the existing model imports (after `import shared.tenant.models`):

```python
import shared.tenant_config.models  # noqa: F401  (register the tenant_configs table on Base.metadata)
```

In `tests/integration/conftest.py`, add the import alongside the existing model imports (after `import shared.tenant.models`):

```python
import shared.tenant_config.models  # noqa: E402, F401
```

- [ ] **Step 3: Generate the migration**

Run: `uv run alembic revision -m "create tenant_configs table"`
This creates `migrations/versions/<rev>_create_tenant_configs_table.py` with `down_revision = '62522466fa91'` auto-filled (the current head). Open the generated file and replace its `upgrade()`/`downgrade()` and imports with:

```python
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# (leave the auto-generated revision / down_revision / branch_labels / depends_on lines as written)


def upgrade() -> None:
    op.create_table(
        "tenant_configs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("DRAFT", "ACTIVE", "ARCHIVED", "REJECTED", name="config_status"),
            nullable=False,
        ),
        sa.Column("business_profile", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("icp", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("signals", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("weights", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("thresholds", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.UniqueConstraint("tenant_id", "version", name="uq_tenant_config_version"),
    )
    op.create_index("ix_tenant_configs_tenant_id", "tenant_configs", ["tenant_id"])
    op.create_index(
        "uq_active_config_per_tenant",
        "tenant_configs",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.create_index(
        "uq_draft_config_per_tenant",
        "tenant_configs",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("status = 'DRAFT'"),
    )


def downgrade() -> None:
    op.drop_index("uq_draft_config_per_tenant", table_name="tenant_configs")
    op.drop_index("uq_active_config_per_tenant", table_name="tenant_configs")
    op.drop_index("ix_tenant_configs_tenant_id", table_name="tenant_configs")
    op.drop_table("tenant_configs")
    sa.Enum(name="config_status").drop(op.get_bind())
```

Keep the `Sequence` import only if the generated header uses it; otherwise leave the file's existing header imports untouched and just paste the `upgrade`/`downgrade` bodies plus the `from sqlalchemy.dialects import postgresql` import.

- [ ] **Step 4: Apply and verify the migration**

First smoke-test that the model imports cleanly:

Run: `uv run python -c "import shared.tenant_config.models; print('ok')"`
Expected: prints `ok` (no import/mapper error). If it errors, fix the model before migrating.

Then apply the migration:

Run: `uv run alembic upgrade head`
Expected: succeeds, no error. Confirm the table and partial indexes exist:

Run: `docker compose exec postgres psql -U postgres -d leadengine -c "\d tenant_configs"`
Expected: shows columns, the `uq_tenant_config_version` constraint, and indexes `uq_active_config_per_tenant`, `uq_draft_config_per_tenant` (both partial, `WHERE status = ...`).

- [ ] **Step 5: Verify downgrade then re-upgrade (round-trip)**

Run: `uv run alembic downgrade -1 && uv run alembic upgrade head`
Expected: both succeed (this proves the `config_status` enum is dropped on downgrade and recreated cleanly).

- [ ] **Step 6: Typecheck and lint**

Run: `uv run mypy shared/tenant_config/models.py migrations/env.py && uv run ruff check shared/tenant_config/models.py`
Expected: PASS. Run `uv run ruff check --fix .` if import ordering is flagged.

- [ ] **Step 7: Commit**

```bash
git add shared/tenant_config/models.py migrations/env.py tests/integration/conftest.py migrations/versions/
git commit -m "feat: add tenant_configs table, model, and migration"
```

---

## Task 5: Service — get_active_config and create_draft

**Files:**
- Create: `shared/tenant_config/service.py`
- Test: `tests/integration/test_tenant_config_service.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/integration/test_tenant_config_service.py`:

```python
"""Integration tests for shared.tenant_config.service against a real Postgres."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import ConflictError, NotFoundError
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


async def _make_tenant(session: AsyncSession) -> UUID:
    tenant = await tenant_service.create_tenant(
        session,
        TenantCreate(
            company_name="Gamoft",
            primary_contact_name="Asha",
            primary_contact_email="asha@gamoft.com",
            business_type=BusinessType.B2B,
        ),
    )
    return tenant.id


async def test_create_draft_assigns_version_one(session: AsyncSession) -> None:
    tenant_id = await _make_tenant(session)

    draft = await service.create_draft(session, tenant_id, _payload())

    assert draft.version == 1
    assert draft.status is ConfigStatus.DRAFT
    assert draft.tenant_id == tenant_id
    assert draft.activated_at is None
    assert draft.weights.fit == 0.2


async def test_second_create_draft_while_draft_exists_raises_conflict(
    session: AsyncSession,
) -> None:
    tenant_id = await _make_tenant(session)
    await service.create_draft(session, tenant_id, _payload())

    with pytest.raises(ConflictError):
        await service.create_draft(session, tenant_id, _payload())


async def test_get_active_config_returns_none_when_no_active(session: AsyncSession) -> None:
    tenant_id = await _make_tenant(session)
    await service.create_draft(session, tenant_id, _payload())  # only a draft, not active

    assert await service.get_active_config(session, tenant_id) is None


async def test_get_active_config_missing_tenant_returns_none(session: AsyncSession) -> None:
    assert await service.get_active_config(session, uuid4()) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_tenant_config_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shared.tenant_config.service'`.

- [ ] **Step 3: Write minimal implementation**

Create `shared/tenant_config/service.py`:

```python
"""Public service for the tenant_config registry — the only path to read,
create, approve, reject, and list per-tenant scoring-config versions.

Holds the invariants scoring depends on: monotonic version numbering and the
atomic activation flip (at most one ACTIVE version per tenant). Policy (what to
build and when to approve) lives in modules/tenant_onboarding.
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import ConflictError, NotFoundError
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


async def create_draft(
    session: AsyncSession, tenant_id: UUID, data: TenantConfigCreate
) -> TenantConfigRead:
    """Write a new DRAFT version for the tenant.

    Raises ConflictError if the tenant already has a DRAFT (one-draft rule).
    """
    existing = await session.execute(
        select(TenantConfig.id).where(
            TenantConfig.tenant_id == tenant_id,
            TenantConfig.status == ConfigStatus.DRAFT,
        )
    )
    if existing.first() is not None:
        raise ConflictError(f"Tenant {tenant_id} already has a draft config")

    config = TenantConfig(
        tenant_id=tenant_id,
        version=await _next_version(session, tenant_id),
        status=ConfigStatus.DRAFT,
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


async def _next_version(session: AsyncSession, tenant_id: UUID) -> int:
    """The next monotonic version number for a tenant (1 if none yet)."""
    result = await session.execute(
        select(func.max(TenantConfig.version)).where(TenantConfig.tenant_id == tenant_id)
    )
    return (result.scalar_one_or_none() or 0) + 1
```

(`NotFoundError` is imported now because Tasks 6–7 use it; if your linter flags it as unused at this step, add it together with the `approve_version` code in Task 6 instead.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_tenant_config_service.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add shared/tenant_config/service.py tests/integration/test_tenant_config_service.py
git commit -m "feat: add tenant_config create_draft and get_active_config"
```

---

## Task 6: Service — approve_version (with rollback)

**Files:**
- Modify: `shared/tenant_config/service.py`
- Test: `tests/integration/test_tenant_config_service.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/integration/test_tenant_config_service.py`:

```python
async def test_approve_draft_makes_it_active(session: AsyncSession) -> None:
    tenant_id = await _make_tenant(session)
    draft = await service.create_draft(session, tenant_id, _payload())

    approved = await service.approve_version(session, draft.id)

    assert approved.status is ConfigStatus.ACTIVE
    assert approved.activated_at is not None
    active = await service.get_active_config(session, tenant_id)
    assert active is not None
    assert active.id == draft.id


async def test_approving_new_version_archives_previous_active(session: AsyncSession) -> None:
    tenant_id = await _make_tenant(session)
    first = await service.create_draft(session, tenant_id, _payload())
    await service.approve_version(session, first.id)  # version 1 active

    second = await service.create_draft(session, tenant_id, _payload())  # version 2 draft
    await service.approve_version(session, second.id)  # promote version 2

    active = await service.get_active_config(session, tenant_id)
    assert active is not None
    assert active.version == 2

    versions = {v.version: v.status for v in await service.list_versions(session, tenant_id)}
    assert versions[1] is ConfigStatus.ARCHIVED
    assert versions[2] is ConfigStatus.ACTIVE


async def test_approve_archived_version_is_rollback(session: AsyncSession) -> None:
    tenant_id = await _make_tenant(session)
    first = await service.create_draft(session, tenant_id, _payload())
    await service.approve_version(session, first.id)
    second = await service.create_draft(session, tenant_id, _payload())
    await service.approve_version(session, second.id)  # version 1 now ARCHIVED

    rolled_back = await service.approve_version(session, first.id)  # re-activate version 1

    assert rolled_back.status is ConfigStatus.ACTIVE
    active = await service.get_active_config(session, tenant_id)
    assert active is not None
    assert active.version == 1


async def test_approve_already_active_raises_conflict(session: AsyncSession) -> None:
    tenant_id = await _make_tenant(session)
    draft = await service.create_draft(session, tenant_id, _payload())
    await service.approve_version(session, draft.id)

    with pytest.raises(ConflictError):
        await service.approve_version(session, draft.id)


async def test_approve_missing_version_raises_not_found(session: AsyncSession) -> None:
    with pytest.raises(NotFoundError):
        await service.approve_version(session, uuid4())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_tenant_config_service.py -v`
Expected: FAIL — `AttributeError: module 'shared.tenant_config.service' has no attribute 'approve_version'` (and `list_versions`, added in Task 7; expect those two tests using `list_versions` to error until Task 7 — the `approve_version` tests should drive this task).

- [ ] **Step 3: Write minimal implementation**

Append to `shared/tenant_config/service.py`:

```python
async def approve_version(session: AsyncSession, config_id: UUID) -> TenantConfigRead:
    """Promote a DRAFT (or roll back to an ARCHIVED) version to ACTIVE.

    Any current ACTIVE version for the same tenant is archived in the same
    transaction. The previous ACTIVE is flushed to ARCHIVED *before* the new row
    is set ACTIVE, so the `uq_active_config_per_tenant` partial unique index is
    never momentarily violated. Raises ConflictError if the target is already
    ACTIVE or is REJECTED; NotFoundError if it does not exist.
    """
    config = await session.get(TenantConfig, config_id)
    if config is None:
        raise NotFoundError(f"Config {config_id} not found")
    if config.status is ConfigStatus.ACTIVE:
        raise ConflictError(f"Config {config_id} is already active")
    if config.status is ConfigStatus.REJECTED:
        raise ConflictError(f"Config {config_id} is rejected and cannot be activated")

    now = datetime.now(UTC)
    current = await session.execute(
        select(TenantConfig).where(
            TenantConfig.tenant_id == config.tenant_id,
            TenantConfig.status == ConfigStatus.ACTIVE,
        )
    )
    active = current.scalar_one_or_none()
    if active is not None:
        active.status = ConfigStatus.ARCHIVED
        active.archived_at = now
        await session.flush()  # archive the old ACTIVE before the new one is set ACTIVE

    config.status = ConfigStatus.ACTIVE
    config.activated_at = now
    config.archived_at = None  # clear if this is a rollback of a previously archived version
    await session.commit()
    await session.refresh(config)
    return TenantConfigRead.model_validate(config)
```

- [ ] **Step 4: Run the approve tests to verify they pass**

Run: `uv run pytest tests/integration/test_tenant_config_service.py -k "approve or archives or rollback" -v`
Expected: PASS for the approve/archive/rollback tests. (Tests calling `list_versions` still fail until Task 7 — that's expected.)

- [ ] **Step 5: Commit**

```bash
git add shared/tenant_config/service.py tests/integration/test_tenant_config_service.py
git commit -m "feat: add tenant_config approve_version with atomic flip and rollback"
```

---

## Task 7: Service — reject_version and list_versions

**Files:**
- Modify: `shared/tenant_config/service.py`
- Test: `tests/integration/test_tenant_config_service.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/integration/test_tenant_config_service.py`:

```python
async def test_reject_draft_marks_it_rejected(session: AsyncSession) -> None:
    tenant_id = await _make_tenant(session)
    draft = await service.create_draft(session, tenant_id, _payload())

    rejected = await service.reject_version(session, draft.id)

    assert rejected.status is ConfigStatus.REJECTED
    assert rejected.archived_at is not None
    assert await service.get_active_config(session, tenant_id) is None


async def test_reject_non_draft_raises_conflict(session: AsyncSession) -> None:
    tenant_id = await _make_tenant(session)
    draft = await service.create_draft(session, tenant_id, _payload())
    await service.approve_version(session, draft.id)  # now ACTIVE, not a draft

    with pytest.raises(ConflictError):
        await service.reject_version(session, draft.id)


async def test_approve_rejected_version_raises_conflict(session: AsyncSession) -> None:
    tenant_id = await _make_tenant(session)
    draft = await service.create_draft(session, tenant_id, _payload())
    await service.reject_version(session, draft.id)

    with pytest.raises(ConflictError):
        await service.approve_version(session, draft.id)


async def test_after_rejecting_a_new_draft_can_be_created(session: AsyncSession) -> None:
    tenant_id = await _make_tenant(session)
    first = await service.create_draft(session, tenant_id, _payload())
    await service.reject_version(session, first.id)

    second = await service.create_draft(session, tenant_id, _payload())
    assert second.version == 2
    assert second.status is ConfigStatus.DRAFT


async def test_list_versions_returns_all_newest_first(session: AsyncSession) -> None:
    tenant_id = await _make_tenant(session)
    first = await service.create_draft(session, tenant_id, _payload())
    await service.approve_version(session, first.id)
    second = await service.create_draft(session, tenant_id, _payload())
    await service.approve_version(session, second.id)

    versions = await service.list_versions(session, tenant_id)

    assert [v.version for v in versions] == [2, 1]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_tenant_config_service.py -v`
Expected: FAIL — `AttributeError: module 'shared.tenant_config.service' has no attribute 'reject_version'` / `list_versions`.

- [ ] **Step 3: Write minimal implementation**

Append to `shared/tenant_config/service.py`:

```python
async def reject_version(session: AsyncSession, config_id: UUID) -> TenantConfigRead:
    """Reject a DRAFT version. Raises ConflictError if it is not a DRAFT."""
    config = await session.get(TenantConfig, config_id)
    if config is None:
        raise NotFoundError(f"Config {config_id} not found")
    if config.status is not ConfigStatus.DRAFT:
        raise ConflictError(
            f"Config {config_id} is not a draft (status {config.status.value})"
        )
    config.status = ConfigStatus.REJECTED
    config.archived_at = datetime.now(UTC)
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
```

- [ ] **Step 4: Run the full service test file to verify it passes**

Run: `uv run pytest tests/integration/test_tenant_config_service.py -v`
Expected: PASS (all integration tests, Tasks 5–7).

- [ ] **Step 5: Typecheck and lint the service**

Run: `uv run mypy shared/tenant_config/service.py tests/integration/test_tenant_config_service.py && uv run ruff check shared/tenant_config/`
Expected: PASS. Run `uv run ruff check --fix .` if import ordering is flagged.

- [ ] **Step 6: Commit**

```bash
git add shared/tenant_config/service.py tests/integration/test_tenant_config_service.py
git commit -m "feat: add tenant_config reject_version and list_versions"
```

---

## Task 8: Remove the obsolete prompt_registry stub and update docs

**Files:**
- Delete: `shared/prompt_registry/`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Confirm the stub is empty and unused**

Run: `cat shared/prompt_registry/__init__.py && grep -rn "prompt_registry" --include="*.py" .`
Expected: `__init__.py` is empty and there are **no** Python imports of `prompt_registry` (only doc/comment mentions). If any real import exists, stop and resolve it before deleting.

- [ ] **Step 2: Delete the stub**

```bash
git rm -r shared/prompt_registry
```

- [ ] **Step 3: Update CLAUDE.md**

Read `CLAUDE.md`, then apply these edits:

a) In the **Layer responsibilities** list, change the `shared/` bullet from listing `tenant`, `tenant_config`, `prompt_registry`, `audit`, `events` to drop `prompt_registry`:

> `- shared/` — cross-cutting domain used by many modules: `tenant`, `tenant_config`, `audit`, `events`.

b) In **Business pipelines → 1. Tenant onboarding**, replace the sentence describing a "versioned scoring prompt committed to `prompt_registry` (`draft → evaluation → active`)" so it reads that the chain builds a versioned `tenant_config` (business profile, ICP, signals, weights, thresholds) with a `DRAFT → ACTIVE → ARCHIVED` lifecycle (human-approved), and re-runs produce a new version that supersedes the prior one without disrupting live scoring. The scoring *prompt template* lives in `modules/scoring` code.

c) In **Business pipelines → 4. Scoring**, replace "Loads the tenant's active prompt + `PersonaObject` + `tenant_config`" with "Loads the tenant's active `tenant_config` version (business profile, ICP, signals, weights, thresholds)".

d) In the **Project status** "Still empty stubs" sentence, remove `prompt_registry` and `tenant_config` from the list of unbuilt `shared/` submodules (leaving `audit`), since `tenant_config` is now built and `prompt_registry` is removed.

- [ ] **Step 4: Verify nothing references the deleted module**

Run: `grep -rn "prompt_registry" --include="*.py" . ; uv run pytest -q`
Expected: no `.py` matches; full suite still green.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "chore: drop prompt_registry stub (merged into tenant_config); update CLAUDE.md"
```

---

## Task 9: Full CI gate

**Files:** none (verification only)

- [ ] **Step 1: Run the local CI gate**

Run: `make ci`
Expected: PASS — `ruff check .` clean, `mypy .` (strict, whole repo incl. tests) clean, full pytest suite green (all prior tests + the new unit and integration tests). Postgres must be running.

- [ ] **Step 2: Fix any failures**

Likely issues and fixes:
- **ruff import ordering** on new files → `make format`, then re-run `make ci`.
- **mypy strict on `_next_version`** → `result.scalar_one_or_none()` is `int | None`; the `(... or 0) + 1` already yields `int`. If mypy complains about the `Any` from JSONB columns, the `Mapped[dict[str, Any]]` annotations match; do not loosen them.
- **mypy on `model_validator` returning `Self`** → ensure `from typing import Self` is imported in `schemas.py`.
- **Pre-existing failure** `test_auth0_settings_have_sensible_defaults` (expects `auth0_domain == ""` but `.env` sets it) is unrelated to this work — leave it; do not "fix" it here.

- [ ] **Step 3: Commit any fixes**

```bash
git add -A
git commit -m "chore: satisfy lint/typecheck for tenant_config slice"
```

(Skip if Step 1 passed clean.)

---

## Self-Review Notes

- **Spec coverage:** versioned table + 4-status lifecycle (T1, T4) · partial unique indexes one-ACTIVE/one-DRAFT (T4, exercised T5–T7) · `business_profile`/`icp` unconstrained JSON, `signals`/`weights`/`thresholds` structured + validated (T2, T3) · binary two-level scoring contract is data-only here, consumed later by scoring (validation in T2/T3 guarantees the shape) · public service surface `get_active_config`/`create_draft`/`approve_version`/`reject_version`/`list_versions` (T5–T7) · atomic activation flip with archive-before-activate ordering (T6) · rollback from ARCHIVED, never REJECTED (T6, T7) · validation on write and read (T3 read coercion test, T5–T7 write) · `prompt_registry` dropped (T8) · deferred items (event/NOTIFY, EVALUATION, cache) explicitly NOT built — no task, per spec. ✅
- **Placeholders:** none — every code step shows complete code; the only non-literal is the generated migration filename `<rev>`, which Alembic fills in (T4 Step 3).
- **Type consistency:** `ConfigStatus`/`Dimension` (StrEnum), `Signal(id,dimension,question)`, `Weights(fit,intent,engagement,behaviour,context)`, `Thresholds(hot,warm)`, `TenantConfigCreate`/`TenantConfigRead`, and service signatures (`get_active_config -> TenantConfigRead | None`, `create_draft/approve_version/reject_version -> TenantConfigRead`, `list_versions -> list[TenantConfigRead]`) are consistent across schema, model, service, and tests. ORM JSONB columns are `Mapped[dict[str, Any]]` / `Mapped[list[dict[str, Any]]]`, coerced back into typed objects by `TenantConfigRead`.
- **Boundary check:** `shared/tenant_config` imports only `core/` and its own submodules; the integration test imports `shared/tenant` (a peer `shared/` module, allowed). No `modules/` import. ✅
