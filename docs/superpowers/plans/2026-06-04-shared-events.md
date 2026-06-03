# shared/events Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Define the contracts-only event vocabulary for cross-module communication — an `Event` envelope, two enums, and four concrete events — as pure pydantic, with no publish/subscribe/bus.

**Architecture:** A single public module `shared/events/schemas.py` holds a frozen `Event` base (the common envelope), the `LeadSource` / `LeadBucket` `StrEnum`s, and four frozen event subclasses (`TenantActivated`, `LeadReceived`, `LeadEnriched`, `LeadScored`). Payloads are thin + routing keys. No delivery mechanism — that lands with the first consumer (`modules/orchestration`, per ADR 0001). Everything is unit-tested; no DB or network.

**Tech Stack:** Python 3.13, pydantic v2 (`BaseModel`, `ConfigDict`, `Field`), `enum.StrEnum`, pytest (`asyncio_mode = auto`, but these tests are sync).

---

## File Structure

- `shared/events/__init__.py` — stays empty (consistent with `shared/tenant/__init__.py`); consumers import the surface directly.
- `shared/events/schemas.py` — **create**; the entire public surface: `Event` base, `LeadSource`, `LeadBucket`, and the four events.
- `tests/unit/test_event_schemas.py` — **create**; pure-validation unit tests in the style of `tests/unit/test_tenant_schemas.py`.

Precedent to follow: `shared/tenant/schemas.py` (StrEnum + pydantic models, module docstring style) and `tests/unit/test_tenant_schemas.py` (test layout, `ValidationError` assertions).

---

## Task 1: The `Event` envelope base

**Files:**
- Create: `shared/events/schemas.py`
- Test: `tests/unit/test_event_schemas.py`

- [ ] **Step 1: Write the failing test**

```python
"""Unit tests for shared.events.schemas — pure validation, no DB."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from shared.events.schemas import Event


class _Sample(Event):
    """A concrete Event subclass used only to exercise the base envelope."""

    event_type: str = "Sample"


def test_event_autopopulates_id_and_timestamp() -> None:
    tenant_id = uuid4()
    evt = _Sample(tenant_id=tenant_id)
    assert isinstance(evt.event_id, UUID)
    assert evt.tenant_id == tenant_id
    assert evt.occurred_at.tzinfo is not None
    assert evt.occurred_at.tzinfo.utcoffset(evt.occurred_at) == UTC.utcoffset(None)


def test_event_instances_get_distinct_ids_and_timestamps() -> None:
    a = _Sample(tenant_id=uuid4())
    b = _Sample(tenant_id=uuid4())
    assert a.event_id != b.event_id


def test_event_requires_tenant_id() -> None:
    with pytest.raises(ValidationError):
        _Sample.model_validate({})


def test_event_is_frozen() -> None:
    evt = _Sample(tenant_id=uuid4())
    with pytest.raises(ValidationError):
        evt.tenant_id = uuid4()  # type: ignore[misc]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_event_schemas.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shared.events.schemas'` (or ImportError for `Event`).

- [ ] **Step 3: Write minimal implementation**

Create `shared/events/schemas.py`:

```python
"""Public event schemas — the typed vocabulary modules use to communicate.

These are contracts only: the Event envelope and the concrete event types.
There is deliberately no publish/subscribe/bus here; delivery lands with the
first consumer (see ADR 0001). Other code imports these types directly, e.g.
`from shared.events.schemas import TenantActivated`.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class Event(BaseModel):
    """Common envelope every event inherits. Immutable and tenant-scoped.

    Subclass this to define a concrete event; never instantiate Event directly.
    """

    model_config = ConfigDict(frozen=True)

    event_id: UUID = Field(default_factory=uuid4)
    event_type: str
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    tenant_id: UUID
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_event_schemas.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add shared/events/schemas.py tests/unit/test_event_schemas.py
git commit -m "feat: add Event envelope base for shared/events"
```

---

## Task 2: The `LeadSource` and `LeadBucket` enums

**Files:**
- Modify: `shared/events/schemas.py`
- Test: `tests/unit/test_event_schemas.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_event_schemas.py` (add the imports `LeadBucket`, `LeadSource` to the existing `from shared.events.schemas import ...` line):

```python
def test_lead_source_membership_is_exact() -> None:
    from shared.events.schemas import LeadSource

    assert {m.value for m in LeadSource} == {
        "GOOGLE_SHEETS",
        "EMAIL",
        "WHATSAPP",
        "INSTAGRAM",
    }


def test_lead_bucket_membership_is_exact() -> None:
    from shared.events.schemas import LeadBucket

    assert {m.value for m in LeadBucket} == {"HOT", "WARM", "COLD"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_event_schemas.py -k "membership" -v`
Expected: FAIL — `ImportError: cannot import name 'LeadSource'`.

- [ ] **Step 3: Write minimal implementation**

In `shared/events/schemas.py`, add `from enum import StrEnum` to the imports, and add these two enums above the `Event` class:

```python
class LeadSource(StrEnum):
    """The four sources lead_ingestion accepts leads from."""

    GOOGLE_SHEETS = "GOOGLE_SHEETS"
    EMAIL = "EMAIL"
    WHATSAPP = "WHATSAPP"
    INSTAGRAM = "INSTAGRAM"


class LeadBucket(StrEnum):
    """The scoring outcome bucket for a lead."""

    HOT = "HOT"
    WARM = "WARM"
    COLD = "COLD"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_event_schemas.py -k "membership" -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add shared/events/schemas.py tests/unit/test_event_schemas.py
git commit -m "feat: add LeadSource and LeadBucket enums"
```

---

## Task 3: `TenantActivated` event

**Files:**
- Modify: `shared/events/schemas.py`
- Test: `tests/unit/test_event_schemas.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_event_schemas.py` (add `TenantActivated` to the import line):

```python
def test_tenant_activated_carries_only_envelope() -> None:
    tenant_id = uuid4()
    evt = TenantActivated(tenant_id=tenant_id)
    assert evt.event_type == "TenantActivated"
    assert evt.tenant_id == tenant_id


def test_tenant_activated_is_frozen() -> None:
    evt = TenantActivated(tenant_id=uuid4())
    with pytest.raises(ValidationError):
        evt.tenant_id = uuid4()  # type: ignore[misc]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_event_schemas.py -k "tenant_activated" -v`
Expected: FAIL — `ImportError: cannot import name 'TenantActivated'`.

- [ ] **Step 3: Write minimal implementation**

In `shared/events/schemas.py`, add below the `Event` class. Use `typing.Literal` for the rename-safe stable `event_type` (add `from typing import Literal` to the imports):

```python
class TenantActivated(Event):
    """A tenant transitioned to ACTIVE; held leads may now drain (ADR 0001)."""

    event_type: Literal["TenantActivated"] = "TenantActivated"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_event_schemas.py -k "tenant_activated" -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add shared/events/schemas.py tests/unit/test_event_schemas.py
git commit -m "feat: add TenantActivated event"
```

---

## Task 4: `LeadReceived` event

**Files:**
- Modify: `shared/events/schemas.py`
- Test: `tests/unit/test_event_schemas.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_event_schemas.py` (add `LeadReceived` to the import line):

```python
def test_lead_received_carries_lead_id_and_source() -> None:
    tenant_id, lead_id = uuid4(), uuid4()
    evt = LeadReceived(tenant_id=tenant_id, lead_id=lead_id, source=LeadSource.EMAIL)
    assert evt.event_type == "LeadReceived"
    assert evt.lead_id == lead_id
    assert evt.source is LeadSource.EMAIL


def test_lead_received_requires_lead_id_and_source() -> None:
    with pytest.raises(ValidationError):
        LeadReceived.model_validate({"tenant_id": str(uuid4())})


def test_lead_received_rejects_invalid_source() -> None:
    with pytest.raises(ValidationError):
        LeadReceived.model_validate(
            {"tenant_id": str(uuid4()), "lead_id": str(uuid4()), "source": "CARRIER_PIGEON"}
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_event_schemas.py -k "lead_received" -v`
Expected: FAIL — `ImportError: cannot import name 'LeadReceived'`.

- [ ] **Step 3: Write minimal implementation**

In `shared/events/schemas.py`, add below `TenantActivated`:

```python
class LeadReceived(Event):
    """lead_ingestion accepted a genuine lead and persisted it; not yet scored."""

    event_type: Literal["LeadReceived"] = "LeadReceived"
    lead_id: UUID
    source: LeadSource
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_event_schemas.py -k "lead_received" -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add shared/events/schemas.py tests/unit/test_event_schemas.py
git commit -m "feat: add LeadReceived event"
```

---

## Task 5: `LeadEnriched` event

**Files:**
- Modify: `shared/events/schemas.py`
- Test: `tests/unit/test_event_schemas.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_event_schemas.py` (add `LeadEnriched` to the import line):

```python
def test_lead_enriched_carries_lead_id() -> None:
    tenant_id, lead_id = uuid4(), uuid4()
    evt = LeadEnriched(tenant_id=tenant_id, lead_id=lead_id)
    assert evt.event_type == "LeadEnriched"
    assert evt.lead_id == lead_id


def test_lead_enriched_requires_lead_id() -> None:
    with pytest.raises(ValidationError):
        LeadEnriched.model_validate({"tenant_id": str(uuid4())})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_event_schemas.py -k "lead_enriched" -v`
Expected: FAIL — `ImportError: cannot import name 'LeadEnriched'`.

- [ ] **Step 3: Write minimal implementation**

In `shared/events/schemas.py`, add below `LeadReceived`:

```python
class LeadEnriched(Event):
    """enrichment finished gathering external data for a lead; ready to score."""

    event_type: Literal["LeadEnriched"] = "LeadEnriched"
    lead_id: UUID
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_event_schemas.py -k "lead_enriched" -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add shared/events/schemas.py tests/unit/test_event_schemas.py
git commit -m "feat: add LeadEnriched event"
```

---

## Task 6: `LeadScored` event (with score range constraint)

**Files:**
- Modify: `shared/events/schemas.py`
- Test: `tests/unit/test_event_schemas.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_event_schemas.py` (add `LeadScored` to the import line):

```python
def test_lead_scored_carries_score_and_bucket() -> None:
    tenant_id, lead_id = uuid4(), uuid4()
    evt = LeadScored(
        tenant_id=tenant_id, lead_id=lead_id, score=82.5, bucket=LeadBucket.HOT
    )
    assert evt.event_type == "LeadScored"
    assert evt.lead_id == lead_id
    assert evt.score == 82.5
    assert evt.bucket is LeadBucket.HOT


def test_lead_scored_accepts_range_boundaries() -> None:
    LeadScored(tenant_id=uuid4(), lead_id=uuid4(), score=0.0, bucket=LeadBucket.COLD)
    LeadScored(tenant_id=uuid4(), lead_id=uuid4(), score=100.0, bucket=LeadBucket.HOT)


@pytest.mark.parametrize("bad_score", [-1.0, 150.0])
def test_lead_scored_rejects_out_of_range_score(bad_score: float) -> None:
    with pytest.raises(ValidationError):
        LeadScored.model_validate(
            {
                "tenant_id": str(uuid4()),
                "lead_id": str(uuid4()),
                "score": bad_score,
                "bucket": "HOT",
            }
        )


def test_lead_scored_rejects_invalid_bucket() -> None:
    with pytest.raises(ValidationError):
        LeadScored.model_validate(
            {
                "tenant_id": str(uuid4()),
                "lead_id": str(uuid4()),
                "score": 50.0,
                "bucket": "LUKEWARM",
            }
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_event_schemas.py -k "lead_scored" -v`
Expected: FAIL — `ImportError: cannot import name 'LeadScored'`.

- [ ] **Step 3: Write minimal implementation**

In `shared/events/schemas.py`, add below `LeadEnriched`:

```python
class LeadScored(Event):
    """scoring produced a final composite score and bucket for a lead."""

    event_type: Literal["LeadScored"] = "LeadScored"
    lead_id: UUID
    score: float = Field(ge=0, le=100)
    bucket: LeadBucket
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_event_schemas.py -k "lead_scored" -v`
Expected: PASS (5 tests, counting the 2 parametrized cases).

- [ ] **Step 5: Commit**

```bash
git add shared/events/schemas.py tests/unit/test_event_schemas.py
git commit -m "feat: add LeadScored event"
```

---

## Task 7: Full-suite gate

**Files:** none (verification only)

- [ ] **Step 1: Run the local CI gate**

Run: `make ci`
Expected: PASS — `ruff check .` clean, `mypy .` (strict, whole repo incl. the new test) clean, full pytest suite green (previous suite + the ~18 new event tests).

- [ ] **Step 2: If anything fails, fix it**

Most likely issues and fixes:
- `mypy` complains about the `# type: ignore[misc]` on frozen-assignment tests — if mypy reports the ignore is *unused*, remove it; if it reports the assignment error, keep it. Adjust to match what mypy actually says.
- `ruff` import ordering — run `make format` to auto-fix, then re-run `make ci`.

- [ ] **Step 3: Commit any fixes**

```bash
git add -A
git commit -m "chore: satisfy lint/typecheck for shared/events"
```

(Skip this commit if Step 1 passed clean.)

---

## Self-Review Notes

- **Spec coverage:** Event envelope (Task 1) · `LeadSource`/`LeadBucket` enums (Task 2) · all four events `TenantActivated`/`LeadReceived`/`LeadEnriched`/`LeadScored` (Tasks 3–6) · frozen/immutable (Tasks 1, 3) · thin+routing-key payloads (Tasks 4, 6) · `score` 0–100 constraint (Task 6) · `event_type` Literal defaults (Tasks 3–6) · empty `__init__.py`, single `schemas.py` file layout (Task 1 creates it, no `__init__.py` edit needed) · all unit, no DB/network (every task) · full-suite + strict mypy gate (Task 7). No publish/subscribe/bus is built — matches the contracts-only scope.
- **Placeholders:** none — every code step shows complete code.
- **Type consistency:** `Event` fields (`event_id`, `event_type`, `occurred_at`, `tenant_id`) referenced consistently; `event_type` is `Literal[...]` per subclass; `lead_id: UUID`, `source: LeadSource`, `score: float`, `bucket: LeadBucket` consistent across tests and implementations.
