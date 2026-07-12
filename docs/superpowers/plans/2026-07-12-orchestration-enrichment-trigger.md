# Orchestration Enrichment-Trigger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make lead ingestion automatically trigger enrichment as a decoupled background job, so a captured lead ends up with `leads.enrichment` populated without any manual step.

**Architecture:** The existing ingestion worker jobs already build a `LeadReceived` but discard it. We add a thin mediator (`modules/orchestration/service.py::process_lead`) that sequences three public services (tenant lookup → enrichment → persistence), wrap it in a new ARQ job (`run_lead_pipeline`), and change the ingestion jobs to enqueue that job with the `LeadReceived` payload. No event bus — ARQ provides the async decoupling; delivery is a direct `enqueue_job`.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2.0 async, ARQ (Redis queue), pydantic v2, pytest (`asyncio_mode = auto`).

## Global Constraints

- **Lite coupling rule:** a module may import another module's **public `service.py`** only, never its internals; no circular dependencies.
- **Python 3.13**; `mypy` runs **strict over the whole repo including `tests/`** — every function (tests too) needs type annotations.
- **`ruff` line length 100** (`E501` ignored).
- **TDD:** failing test first, watch it fail, minimal code, pass, commit.
- **Unit tests must not touch a DB or network.** Integration tests may use real Postgres (`DATABASE_URL`) and must stub network calls.
- `pytest` runs with `asyncio_mode = auto` — async test functions need **no** decorator.
- **Commits:** `feat:` / `test:` prefixes; end the body with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Work on branch `feature/orchestration-enrichment-trigger` (already checked out).

---

### Task 1: `process_lead` mediator

**Files:**
- Create: `modules/orchestration/service.py`
- Test: `tests/unit/test_orchestration_service.py`

**Interfaces:**
- Consumes (existing, unchanged): `shared.tenant.service.get_tenant(session, tenant_id) -> Tenant`; `modules.enrichment.service.run_enrichment(lead: LeadPayload, tenant: TenantRead, deps=None) -> EnrichmentResult`; `modules.lead_ingestion.service.store_lead_enrichment(session, lead_id: UUID, result: EnrichmentResult) -> Lead`; `shared.events.schemas.LeadReceived` (fields: `tenant_id: UUID`, `lead_id: UUID`, `source`, `payload: LeadPayload`); `shared.tenant.schemas.TenantRead` (`model_config = ConfigDict(from_attributes=True)`).
- Produces: `modules.orchestration.service.process_lead(session: AsyncSession, event: LeadReceived) -> None`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_orchestration_service.py`:

```python
"""Unit tests for modules/orchestration/service.process_lead — all deps mocked."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from modules.orchestration import service
from shared.events.schemas import LeadPayload, LeadReceived, LeadSource
from shared.tenant.schemas import (
    BusinessType,
    KybStatus,
    OnboardingStatus,
    TenantRead,
    TenantStatus,
)


def _tenant_read() -> TenantRead:
    return TenantRead(
        id=uuid4(),
        company_name="Acme",
        primary_contact_name="Admin",
        primary_contact_email="admin@acme.com",
        business_type=BusinessType.B2B,
        website_url="https://acme.com",
        pan="ABCDE1234F",
        kyb_status=KybStatus.VERIFIED,
        onboarding_status=OnboardingStatus.COMPLETE,
        status=TenantStatus.ACTIVE,
        timezone="UTC",
        language_preference="en",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        activated_at=None,
    )


def _event() -> LeadReceived:
    return LeadReceived(
        tenant_id=uuid4(),
        lead_id=uuid4(),
        source=LeadSource.EMAIL,
        payload=LeadPayload(name="Priya", email="p@acme.com", source=LeadSource.EMAIL),
    )


async def test_process_lead_enriches_then_stores(monkeypatch: pytest.MonkeyPatch) -> None:
    event = _event()
    tenant = _tenant_read()
    result = MagicMock(name="EnrichmentResult")

    get_tenant = AsyncMock(return_value=tenant)
    run_enrichment = AsyncMock(return_value=result)
    store = AsyncMock()
    monkeypatch.setattr(service, "get_tenant", get_tenant)
    monkeypatch.setattr(service, "run_enrichment", run_enrichment)
    monkeypatch.setattr(service, "store_lead_enrichment", store)

    session = MagicMock(name="session")
    await service.process_lead(session, event)

    get_tenant.assert_awaited_once_with(session, event.tenant_id)
    # enrichment gets the event payload and a TenantRead carrying the tenant's business_type
    assert run_enrichment.await_args.args[0] == event.payload
    passed_tenant = run_enrichment.await_args.args[1]
    assert isinstance(passed_tenant, TenantRead)
    assert passed_tenant.business_type is BusinessType.B2B
    # result is persisted onto the lead row
    store.assert_awaited_once_with(session, event.lead_id, result)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_orchestration_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'modules.orchestration.service'` (only `__init__.py` exists).

- [ ] **Step 3: Write minimal implementation**

Create `modules/orchestration/service.py`:

```python
"""Orchestration: drive a received lead through the enrichment step.

process_lead is the pipeline mediator — it sequences public services across
modules (tenant lookup → enrichment → persistence). It is the seam where the
hold/drain gate (before enrichment) and scoring (after) will slot in later.
Under the lite coupling rule it may import other modules' public service.py.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from modules.enrichment.service import run_enrichment
from modules.lead_ingestion.service import store_lead_enrichment
from shared.events.schemas import LeadReceived
from shared.tenant.schemas import TenantRead
from shared.tenant.service import get_tenant


async def process_lead(session: AsyncSession, event: LeadReceived) -> None:
    """Enrich the lead carried on `event` and persist the result on its row.

    Assumes the tenant is ACTIVE (the hold/drain gate is not built yet).
    Idempotent: store_lead_enrichment overwrites, so a redelivered event is safe.
    """
    tenant = await get_tenant(session, event.tenant_id)
    result = await run_enrichment(event.payload, TenantRead.model_validate(tenant))
    await store_lead_enrichment(session, event.lead_id, result)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_orchestration_service.py -v`
Expected: PASS.

- [ ] **Step 5: Typecheck + lint**

Run: `uv run mypy modules/orchestration/service.py tests/unit/test_orchestration_service.py && uv run ruff check modules/orchestration/service.py tests/unit/test_orchestration_service.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add modules/orchestration/service.py tests/unit/test_orchestration_service.py
git commit -m "feat: add orchestration.process_lead enrichment mediator

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `run_lead_pipeline` ARQ job + registration

**Files:**
- Create: `workers/jobs/orchestration.py`
- Modify: `workers/worker.py` (import + add to `WorkerSettings.functions`)
- Test: `tests/integration/test_orchestration_pipeline.py`
- Test: `tests/unit/test_worker_registration.py`

**Interfaces:**
- Consumes: `modules.orchestration.service.process_lead` (Task 1); `ctx["session_factory"]: async_sessionmaker[AsyncSession]`; a JSON-serialised `LeadReceived` dict.
- Produces: `workers.jobs.orchestration.run_lead_pipeline(ctx: dict[str, object], event_dict: dict[str, Any]) -> None`, registered by the ARQ name `"run_lead_pipeline"`.

- [ ] **Step 1: Write the failing integration test**

Create `tests/integration/test_orchestration_pipeline.py`:

```python
"""Integration: run_lead_pipeline enriches a real lead row (research stubbed)."""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from core.config import get_settings
from modules.enrichment.schemas import ResearchFindings
from modules.lead_ingestion.service import Lead
from shared.events.schemas import LeadPayload, LeadReceived, LeadSource
from shared.tenant.schemas import BusinessType, TenantCreate
from shared.tenant import service as tenant_service
from workers.jobs.orchestration import run_lead_pipeline


async def _seed_tenant_and_lead(session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    tenant = await tenant_service.create_tenant(
        session,
        TenantCreate(
            company_name=f"Enrich Co {uuid.uuid4().hex[:6]}",
            primary_contact_name="Admin",
            primary_contact_email=f"admin_{uuid.uuid4().hex[:6]}@enrich.com",
            business_type=BusinessType.B2B,
            website_url="https://enrich.com",  # type: ignore[arg-type]
            pan="AAACX1234C",
            pan_holder_name="Test Holder Pvt Ltd",
            pan_dob="01/04/2019",
            consent=True,
        ),
    )
    lead = Lead(
        tenant_id=tenant.id,
        pipeline_stage="received",
        source_channel="EMAIL",
        full_name="Jane Doe",
        email="jane@fake.invalid",
        phone="+15550001234",
        raw_event_json={"demo": True},
        extra_fields={"company": "Stripe"},
    )
    session.add(lead)
    await session.commit()
    return tenant.id, lead.id


async def test_run_lead_pipeline_populates_enrichment(
    session: AsyncSession, monkeypatch: Any
) -> None:
    tenant_id, lead_id = await _seed_tenant_and_lead(session)

    from unittest.mock import AsyncMock

    findings = ResearchFindings(company_info={"industry": "SaaS"}, confidence=0.8)
    monkeypatch.setattr(
        "modules.enrichment.service.research", AsyncMock(return_value=findings)
    )

    event = LeadReceived(
        tenant_id=tenant_id,
        lead_id=lead_id,
        source=LeadSource.EMAIL,
        payload=LeadPayload(name="Jane Doe", company="Stripe", source=LeadSource.EMAIL),
    )

    engine = create_async_engine(get_settings().database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ctx: dict[str, object] = {"session_factory": factory}
    try:
        await run_lead_pipeline(ctx, event.model_dump(mode="json"))
    finally:
        await engine.dispose()

    session.expunge_all()
    fresh = await session.get(Lead, lead_id)
    assert fresh is not None
    assert fresh.enriched_at is not None
    assert fresh.enrichment is not None
    assert fresh.enrichment["company_info"] == {"industry": "SaaS"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_orchestration_pipeline.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'workers.jobs.orchestration'`.

- [ ] **Step 3: Write minimal implementation**

Create `workers/jobs/orchestration.py`:

```python
"""ARQ job: drive a received lead through the enrichment pipeline."""

from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from modules.orchestration.service import process_lead
from shared.events.schemas import LeadReceived


async def run_lead_pipeline(ctx: dict[str, object], event_dict: dict[str, Any]) -> None:
    """Rebuild the LeadReceived, enrich the lead, and persist the result."""
    event = LeadReceived.model_validate(event_dict)
    factory = cast(async_sessionmaker[AsyncSession], ctx["session_factory"])
    async with factory() as session:
        await process_lead(session, event)
        await session.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_orchestration_pipeline.py -v`
Expected: PASS. (Requires Postgres up: `make up` / `DATABASE_URL` set.)

- [ ] **Step 5: Register the job in the worker**

Modify `workers/worker.py` — add the import alongside the other job imports:

```python
from workers.jobs.orchestration import run_lead_pipeline
```

and add `run_lead_pipeline` to the `WorkerSettings.functions` list:

```python
    functions = [
        run_onboarding_pipeline,
        run_lead_capture_batch,
        run_lead_capture,
        run_lead_ad_capture,
        run_lead_pipeline,
    ]
```

- [ ] **Step 6: Write the registration guard test**

Create `tests/unit/test_worker_registration.py`:

```python
"""Guard: the enrichment pipeline job is registered with the ARQ worker."""

from workers.jobs.orchestration import run_lead_pipeline
from workers.worker import WorkerSettings


def test_run_lead_pipeline_is_registered() -> None:
    assert run_lead_pipeline in WorkerSettings.functions
```

- [ ] **Step 7: Run both new tests + typecheck**

Run: `uv run pytest tests/unit/test_worker_registration.py tests/integration/test_orchestration_pipeline.py -v && uv run mypy workers/jobs/orchestration.py workers/worker.py tests/integration/test_orchestration_pipeline.py tests/unit/test_worker_registration.py`
Expected: PASS, no mypy errors.

- [ ] **Step 8: Commit**

```bash
git add workers/jobs/orchestration.py workers/worker.py tests/integration/test_orchestration_pipeline.py tests/unit/test_worker_registration.py
git commit -m "feat: add run_lead_pipeline ARQ job and register it

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Publish seam — ingestion jobs enqueue enrichment

**Files:**
- Modify: `workers/jobs/lead_ingestion.py` (add helper; capture + dispatch at 3 call sites)
- Test: `tests/unit/test_enrichment_dispatch.py`

**Interfaces:**
- Consumes: `ctx["redis"]: ArqRedis` (ARQ injects it); the `"run_lead_pipeline"` job name (Task 2); `run_capture` / `run_capture_message` returning `(Lead, LeadReceived | None)`.
- Produces: `workers.jobs.lead_ingestion._dispatch_enrichment(ctx: dict[str, object], received: LeadReceived | None) -> None`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_enrichment_dispatch.py`:

```python
"""Unit tests for the ingestion→enrichment enqueue helper."""

from unittest.mock import AsyncMock
from uuid import uuid4

from shared.events.schemas import LeadPayload, LeadReceived, LeadSource
from workers.jobs import lead_ingestion as li


def _received() -> LeadReceived:
    return LeadReceived(
        tenant_id=uuid4(),
        lead_id=uuid4(),
        source=LeadSource.EMAIL,
        payload=LeadPayload(name="Priya", source=LeadSource.EMAIL),
    )


async def test_dispatch_enqueues_pipeline_for_received_event() -> None:
    received = _received()
    pool = AsyncMock()
    ctx: dict[str, object] = {"redis": pool}

    await li._dispatch_enrichment(ctx, received)

    pool.enqueue_job.assert_awaited_once()
    call = pool.enqueue_job.await_args
    assert call is not None  # narrow _Call | None for mypy strict
    assert call.args[0] == "run_lead_pipeline"
    assert call.args[1] == received.model_dump(mode="json")
    assert call.kwargs["_job_id"] == f"enrich:{received.lead_id}"


async def test_dispatch_is_noop_when_no_event() -> None:
    pool = AsyncMock()
    ctx: dict[str, object] = {"redis": pool}

    await li._dispatch_enrichment(ctx, None)

    pool.enqueue_job.assert_not_awaited()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_enrichment_dispatch.py -v`
Expected: FAIL — `AttributeError: module 'workers.jobs.lead_ingestion' has no attribute '_dispatch_enrichment'`.

- [ ] **Step 3: Add the helper**

In `workers/jobs/lead_ingestion.py`, add the import near the top (with the other `arq` / `shared.events` imports):

```python
from arq.connections import ArqRedis
from shared.events.schemas import LeadReceived, LeadSource
```

(The file already imports `LeadSource` — merge, don't duplicate the line.) Then add the helper after the module logger:

```python
async def _dispatch_enrichment(ctx: dict[str, object], received: LeadReceived | None) -> None:
    """Enqueue the enrichment pipeline for a freshly captured lead.

    De-duped by the `enrich:{lead_id}` job id so a redelivered capture does not
    trigger a second enrichment while the first is still queued/running.
    """
    if received is None:
        return
    pool = cast(ArqRedis, ctx["redis"])
    await pool.enqueue_job(
        "run_lead_pipeline",
        received.model_dump(mode="json"),
        _job_id=f"enrich:{received.lead_id}",
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_enrichment_dispatch.py -v`
Expected: PASS.

- [ ] **Step 5: Wire the helper into the three capture call sites**

In `workers/jobs/lead_ingestion.py`, replace each discarded capture result with a captured tuple + dispatch.

`run_lead_capture` (message job) — the line `await run_capture_message(session, event)` becomes:

```python
        _lead, received = await run_capture_message(session, event)
        await _dispatch_enrichment(ctx, received)
```

`run_lead_ad_capture` — the line `await run_capture(session, event)` becomes:

```python
        _lead, received = await run_capture(session, event)
        await _dispatch_enrichment(ctx, received)
```

`run_lead_capture_batch` — inside the `else:` branch, `await run_capture(session, event)` becomes:

```python
                _lead, received = await run_capture(session, event)
                await _dispatch_enrichment(ctx, received)
```

- [ ] **Step 6: Run the full lead_ingestion + new suites to confirm no regressions**

Run: `uv run pytest tests/unit/test_enrichment_dispatch.py tests/integration/lead_ingestion -v`
Expected: PASS (existing golden-path tests drive `pipeline.run_capture` directly and are unaffected; `test_lead_ad_capture_failure.py` hits the failure path before dispatch).

- [ ] **Step 7: Typecheck + lint**

Run: `uv run mypy workers/jobs/lead_ingestion.py tests/unit/test_enrichment_dispatch.py && uv run ruff check workers/jobs/lead_ingestion.py tests/unit/test_enrichment_dispatch.py`
Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add workers/jobs/lead_ingestion.py tests/unit/test_enrichment_dispatch.py
git commit -m "feat: ingestion enqueues enrichment on capture

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Full-suite gate + manual E2E

**Files:** none (verification only).

- [ ] **Step 1: Run the local CI gate**

Run: `make ci`
Expected: lint + typecheck + full pytest all green.

- [ ] **Step 2: Manual end-to-end (requires `OPENROUTER_API_KEY` set in `.env` — see spec prerequisite)**

```bash
docker compose up -d postgres redis mcp-web-search
make migrate
docker compose run --rm -e PYTHONPATH=/app -e PYTHONUNBUFFERED=1 \
    app /app/.venv/bin/python scripts/enrich_lead_demo.py
```

Expected: `[demo] LIVE enrichment succeeded` and a populated `company_info` in the read-back row. (This exercises `run_enrichment` + persistence directly; the ingestion→`run_lead_pipeline` link is covered by the Task 2 integration test and the Task 3 dispatch tests.)

- [ ] **Step 3: (Optional) push + open PR**

```bash
git push -u origin feature/orchestration-enrichment-trigger
gh pr create --fill
```

Do not merge your own PR without a teammate's approval and green CI.

---

## Coverage / scope notes

- **Out of scope (deferred, per spec):** hold/drain gate, `LeadEnriched` emission, scoring, notification, the event bus, Shopify `EnrichmentDeps` (`run_enrichment` is called with `deps=None`).
- **Known coverage boundary:** the three capture call sites are wired by the same `_dispatch_enrichment` helper, which is unit-tested directly; the individual job bodies are not each re-tested end-to-end (their existing tests target `pipeline.run_capture`). The full chain is validated by the Task 2 integration test plus the manual E2E.
- **Prerequisite (orthogonal):** live enrichment needs `OPENROUTER_API_KEY` set; `core/config.py`'s production guard also still requires the dead `groq_api_key` instead of `openrouter_api_key`. Tracked separately.
