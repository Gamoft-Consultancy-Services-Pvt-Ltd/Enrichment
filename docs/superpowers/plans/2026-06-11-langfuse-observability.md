# Langfuse AI Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Self-hosted Langfuse v2 traces every LLM call and every onboarding pipeline run, tagged by tenant, with zero per-module plumbing for future pipelines.

**Architecture:** One `langfuse/langfuse:2` container joins the existing compose stack, storing data in a separate `langfuse` database inside the existing Postgres container. The Python side is instrumented at two choke points: `clients/groq_client.call_with_tool` (`@observe(as_type="generation")` — every LLM call in any module becomes a traced generation) and `modules/tenant_onboarding/pipeline.run_pipeline` (`@observe()` — one trace per onboarding run, tagged with `tenant_id`). A `core/observability.py` bootstrap configures or disables the SDK from settings; the ARQ worker flushes buffered traces on shutdown.

**Tech Stack:** `langfuse>=2.60,<3` Python SDK (decorators API), Docker Compose, pydantic-settings, ARQ lifecycle hooks.

**Spec:** `docs/superpowers/specs/2026-06-11-langfuse-observability-design.md`

**Conventions that apply to every task:**
- All commands run inside Docker: `docker compose exec app …` (start the stack first with `docker compose up -d` if needed).
- TDD: write the failing test, watch it fail for the right reason, then minimum code.
- mypy is strict over the whole repo including tests; `pyproject.toml` already has `ignore_missing_imports = true` and `disallow_untyped_decorators = false`, so the langfuse SDK needs no mypy config changes.

---

## File structure

| File | Action | Responsibility |
|---|---|---|
| `pyproject.toml` | Modify | Add `langfuse>=2.60,<3` dependency |
| `core/config.py` | Modify | 3 new settings: `langfuse_public_key`, `langfuse_secret_key`, `langfuse_host` |
| `core/observability.py` | Create | Configure/disable Langfuse SDK from settings; flush helper |
| `clients/groq_client.py` | Modify | `@observe(as_type="generation")` + record model/input/output/usage |
| `modules/tenant_onboarding/pipeline.py` | Modify | `@observe()` on `run_pipeline` + tenant_id trace tagging |
| `workers/worker.py` | Modify | ARQ `on_startup` (configure) / `on_shutdown` (flush) hooks |
| `docker-compose.yml` | Modify | `langfuse` service |
| `.env.example` | Modify | Langfuse keys/host; drop stale `SERPAPI_API_KEY` |
| `tests/unit/test_config.py` | Modify | Settings tests |
| `tests/unit/test_observability.py` | Create | Bootstrap wiring tests |
| `tests/unit/test_groq_client.py` | Modify | Generation-recording test |
| `tests/unit/test_onboarding_pipeline.py` | Modify | Trace-tagging test |
| `tests/unit/test_worker.py` | Modify (create if absent) | Lifecycle-hook test |

---

### Task 1: Langfuse settings in `core/config.py`

**Files:**
- Modify: `core/config.py`
- Test: `tests/unit/test_config.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_config.py`:

```python
def test_settings_langfuse_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Langfuse keys default to empty (tracing disabled); host to the Docker service."""
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_HOST", raising=False)

    settings = build_settings()

    assert settings.langfuse_public_key == ""
    assert settings.langfuse_secret_key == ""
    assert settings.langfuse_host == "http://langfuse:3000"


def test_settings_langfuse_overridable() -> None:
    settings = build_settings(
        langfuse_public_key="pk-lf-test",
        langfuse_secret_key="sk-lf-test",
        langfuse_host="http://localhost:3000",
    )

    assert settings.langfuse_public_key == "pk-lf-test"
    assert settings.langfuse_secret_key == "sk-lf-test"
    assert settings.langfuse_host == "http://localhost:3000"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose exec app uv run pytest tests/unit/test_config.py -v`
Expected: the two new tests FAIL with errors mentioning `langfuse_public_key` (unknown attribute/argument).

- [ ] **Step 3: Add the settings fields**

In `core/config.py`, directly below `serper_api_key: str = ""`:

```python
    # Langfuse (self-hosted AI observability). Empty keys disable tracing entirely.
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "http://langfuse:3000"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose exec app uv run pytest tests/unit/test_config.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add core/config.py tests/unit/test_config.py
git commit -m "feat: add langfuse settings to core config"
```

---

### Task 2: Add the `langfuse` dependency

**Files:**
- Modify: `pyproject.toml`, `uv.lock`

- [ ] **Step 1: Add the dependency (v2 SDK pinned — the v3 SDK changed APIs)**

Run: `docker compose exec app uv add "langfuse>=2.60,<3"`
Expected: `pyproject.toml` gains `"langfuse>=2.60,<3"` in `dependencies`, `uv.lock` updates, install succeeds.

- [ ] **Step 2: Verify the decorators module imports**

Run: `docker compose exec app uv run python -c "from langfuse.decorators import observe, langfuse_context; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: add langfuse v2 sdk dependency"
```

---

### Task 3: `core/observability.py` bootstrap

**Files:**
- Create: `core/observability.py`
- Test: `tests/unit/test_observability.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_observability.py`:

```python
"""Unit tests for core/observability — Langfuse SDK wiring, no network."""

from unittest.mock import patch

from core.observability import configure_langfuse, flush_langfuse
from tests.helpers import build_settings


def test_configure_disables_tracing_when_keys_missing() -> None:
    """Empty keys (the default) must explicitly disable the SDK — silent no-op."""
    settings = build_settings(langfuse_public_key="", langfuse_secret_key="")

    with patch("core.observability.langfuse_context") as mock_ctx:
        configure_langfuse(settings)

    mock_ctx.configure.assert_called_once_with(enabled=False)


def test_configure_enables_tracing_when_keys_present() -> None:
    settings = build_settings(
        langfuse_public_key="pk-lf-test",
        langfuse_secret_key="sk-lf-test",
        langfuse_host="http://langfuse:3000",
    )

    with patch("core.observability.langfuse_context") as mock_ctx:
        configure_langfuse(settings)

    mock_ctx.configure.assert_called_once_with(
        public_key="pk-lf-test",
        secret_key="sk-lf-test",
        host="http://langfuse:3000",
        enabled=True,
    )


def test_flush_delegates_to_sdk() -> None:
    with patch("core.observability.langfuse_context") as mock_ctx:
        flush_langfuse()

    mock_ctx.flush.assert_called_once_with()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose exec app uv run pytest tests/unit/test_observability.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.observability'`.

- [ ] **Step 3: Write the implementation**

Create `core/observability.py`:

```python
"""Langfuse observability bootstrap.

Configures the Langfuse SDK from settings. With no keys configured (the
default), tracing is explicitly disabled so every `@observe()` decorator in
the codebase becomes a silent no-op — unit tests and local runs without
Langfuse behave exactly as before.
"""

from langfuse.decorators import langfuse_context

from core.config import Settings, get_settings


def configure_langfuse(settings: Settings | None = None) -> None:
    """Enable Langfuse tracing if keys are configured, otherwise disable it."""
    settings = settings or get_settings()
    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        langfuse_context.configure(enabled=False)
        return
    langfuse_context.configure(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
        enabled=True,
    )


def flush_langfuse() -> None:
    """Deliver buffered traces now (the SDK batches and sends asynchronously)."""
    langfuse_context.flush()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose exec app uv run pytest tests/unit/test_observability.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add core/observability.py tests/unit/test_observability.py
git commit -m "feat: add langfuse observability bootstrap in core"
```

---

### Task 4: Instrument `clients/groq_client.py`

**Files:**
- Modify: `clients/groq_client.py`
- Test: `tests/unit/test_groq_client.py`

- [ ] **Step 1: Write the failing test**

In `tests/unit/test_groq_client.py`, first extend the existing `_make_tool_call_response` helper so the mock response carries token usage — replace its final lines:

```python
    response = MagicMock()
    response.choices = [choice]
    return response
```

with:

```python
    response = MagicMock()
    response.choices = [choice]
    response.usage.prompt_tokens = 100
    response.usage.completion_tokens = 50
    return response
```

Then append the new test:

```python
async def test_call_with_tool_records_langfuse_generation() -> None:
    """The Langfuse observation gets model, prompt, structured output, and token usage."""
    expected = {"industry": "SaaS"}
    mock_create = AsyncMock(return_value=_make_tool_call_response("my_tool", expected))

    with (
        patch("clients.groq_client.AsyncGroq") as mock_client_cls,
        patch("clients.groq_client.langfuse_context") as mock_ctx,
    ):
        mock_client_cls.return_value.chat.completions.create = mock_create
        await call_with_tool(
            prompt="test prompt",
            tool_name="my_tool",
            tool_description="desc",
            input_schema={"type": "object", "properties": {}, "required": []},
        )

    mock_ctx.update_current_observation.assert_called_once()
    kwargs = mock_ctx.update_current_observation.call_args.kwargs
    assert kwargs["name"] == "my_tool"
    assert kwargs["model"] == "llama-3.3-70b-versatile"
    assert kwargs["input"] == "test prompt"
    assert kwargs["output"] == expected
    assert kwargs["usage"] == {"input": 100, "output": 50}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec app uv run pytest tests/unit/test_groq_client.py -v`
Expected: new test FAILS with `AttributeError: <module 'clients.groq_client'> does not have the attribute 'langfuse_context'` (not imported yet); existing tests still PASS.

- [ ] **Step 3: Instrument the client**

In `clients/groq_client.py`:

Add the import (third-party block, after `from groq import AsyncGroq`):

```python
from langfuse.decorators import langfuse_context, observe
```

Decorate the function:

```python
@observe(as_type="generation")
async def call_with_tool(
```

And replace the final two lines of the function:

```python
    result: dict[str, Any] = json.loads(tool_calls[0].function.arguments)
    return result
```

with:

```python
    result: dict[str, Any] = json.loads(tool_calls[0].function.arguments)
    langfuse_context.update_current_observation(
        name=tool_name,
        model=model,
        input=prompt,
        output=result,
        usage={
            "input": response.usage.prompt_tokens,
            "output": response.usage.completion_tokens,
        },
    )
    return result
```

- [ ] **Step 4: Run the file's tests, then the full unit suite**

Run: `docker compose exec app uv run pytest tests/unit/test_groq_client.py -v`
Expected: all PASS.

Run: `docker compose exec app uv run pytest tests/unit -q`
Expected: all PASS — with no keys configured the decorator no-ops, so agent and pipeline tests are unaffected.

- [ ] **Step 5: Commit**

```bash
git add clients/groq_client.py tests/unit/test_groq_client.py
git commit -m "feat: trace every groq call as a langfuse generation"
```

---

### Task 5: Instrument `modules/tenant_onboarding/pipeline.py`

**Files:**
- Modify: `modules/tenant_onboarding/pipeline.py`
- Test: `tests/unit/test_onboarding_pipeline.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_onboarding_pipeline.py` (mirrors the mock plumbing of `test_pipeline_happy_path_sets_complete`, adding a `langfuse_context` patch):

```python
async def test_pipeline_tags_trace_with_tenant_id(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant_id = uuid4()
    mock_session = AsyncMock()

    business_profile = {"industry": "SaaS", "target_market": "SMB"}
    icp_data = {"buyer_role": "VP Sales", "company_size": "50-200"}
    from shared.tenant_config.schemas import Dimension, Signal, Thresholds, Weights

    sigs = [Signal(id=f"{d.value.lower()}_1", dimension=d, question="?") for d in Dimension]
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
        "modules.tenant_onboarding.pipeline.search_site_pages",
        AsyncMock(return_value=[]),
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
    monkeypatch.setattr("modules.tenant_onboarding.pipeline.create_active", AsyncMock())
    monkeypatch.setattr("modules.tenant_onboarding.pipeline.activate_tenant", AsyncMock())

    mock_http_response = MagicMock()
    mock_http_response.status_code = 200
    mock_http_response.text = "<html><body>Acme sells CRM</body></html>"
    mock_http_response.raise_for_status = MagicMock()

    with (
        patch("modules.tenant_onboarding.pipeline.httpx.AsyncClient") as mock_http,
        patch("modules.tenant_onboarding.pipeline.langfuse_context") as mock_ctx,
    ):
        mock_http.return_value.__aenter__.return_value.get = AsyncMock(
            return_value=mock_http_response
        )
        await run_pipeline(mock_session, tenant_id)

    mock_ctx.update_current_trace.assert_called_once_with(
        name="tenant-onboarding",
        metadata={"tenant_id": str(tenant_id)},
        tags=[str(tenant_id)],
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec app uv run pytest tests/unit/test_onboarding_pipeline.py::test_pipeline_tags_trace_with_tenant_id -v`
Expected: FAIL with `AttributeError: <module 'modules.tenant_onboarding.pipeline'> does not have the attribute 'langfuse_context'`.

- [ ] **Step 3: Instrument the pipeline**

In `modules/tenant_onboarding/pipeline.py`:

Add the import (third-party block, after `import httpx`):

```python
from langfuse.decorators import langfuse_context, observe
```

Decorate `run_pipeline` and tag the trace as its first statement. `capture_input=False` because the SQLAlchemy session argument is not serializable; the trace's useful context is set explicitly instead:

```python
@observe(capture_input=False)
async def run_pipeline(session: AsyncSession, tenant_id: UUID) -> None:
    """Run the full onboarding pipeline: website fetch → 3 agents → activate."""
    langfuse_context.update_current_trace(
        name="tenant-onboarding",
        metadata={"tenant_id": str(tenant_id)},
        tags=[str(tenant_id)],
    )
    await set_onboarding_status(session, tenant_id, OnboardingStatus.RUNNING)
```

(The rest of the function body is unchanged.)

- [ ] **Step 4: Run the file's tests, then the full unit suite**

Run: `docker compose exec app uv run pytest tests/unit/test_onboarding_pipeline.py -v`
Expected: all PASS (existing tests unaffected — disabled SDK no-ops).

Run: `docker compose exec app uv run pytest tests/unit -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add modules/tenant_onboarding/pipeline.py tests/unit/test_onboarding_pipeline.py
git commit -m "feat: trace onboarding runs in langfuse tagged by tenant"
```

---

### Task 6: Worker lifecycle hooks (configure on startup, flush on shutdown)

**Files:**
- Modify: `workers/worker.py`
- Test: `tests/unit/test_worker.py` (create if it does not exist)

- [ ] **Step 1: Write the failing test**

If `tests/unit/test_worker.py` does not exist, create it with this content; otherwise append the test functions and merge the imports:

```python
"""Unit tests for workers/worker — ARQ settings wiring, no Redis needed."""

from unittest.mock import patch

from workers.worker import WorkerSettings, shutdown, startup


def test_worker_registers_langfuse_lifecycle_hooks() -> None:
    """Configure tracing when the worker starts; flush buffered traces on shutdown."""
    assert WorkerSettings.on_startup is startup
    assert WorkerSettings.on_shutdown is shutdown


async def test_startup_configures_langfuse() -> None:
    with patch("workers.worker.configure_langfuse") as mock_configure:
        await startup({})

    mock_configure.assert_called_once_with()


async def test_shutdown_flushes_langfuse() -> None:
    with patch("workers.worker.flush_langfuse") as mock_flush:
        await shutdown({})

    mock_flush.assert_called_once_with()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec app uv run pytest tests/unit/test_worker.py -v`
Expected: FAIL with `ImportError: cannot import name 'startup'` (or `'shutdown'`).

- [ ] **Step 3: Write the implementation**

Replace `workers/worker.py` with:

```python
"""ARQ worker entry point — registers all background jobs."""

from typing import Any

from arq.connections import RedisSettings

from core.config import get_settings
from core.observability import configure_langfuse, flush_langfuse
from workers.jobs.onboarding import run_onboarding_pipeline


async def startup(ctx: dict[str, Any]) -> None:
    """Configure Langfuse tracing for this worker process."""
    configure_langfuse()


async def shutdown(ctx: dict[str, Any]) -> None:
    """Flush buffered Langfuse traces before the worker exits."""
    flush_langfuse()


class WorkerSettings:
    """ARQ worker configuration. Run with: uv run arq workers.worker.WorkerSettings"""

    functions = [run_onboarding_pipeline]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose exec app uv run pytest tests/unit/test_worker.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add workers/worker.py tests/unit/test_worker.py
git commit -m "feat: wire langfuse configure/flush into arq worker lifecycle"
```

---

### Task 7: Langfuse service in docker-compose + env examples

**Files:**
- Modify: `docker-compose.yml`
- Modify: `.env.example`

- [ ] **Step 1: Create the `langfuse` database in the existing Postgres container**

(The Postgres volume already exists, so `docker-entrypoint-initdb.d` will not run — create the DB directly. Langfuse runs its own migrations on first start.)

Run: `docker compose exec postgres psql -U postgres -c "CREATE DATABASE langfuse;"`
Expected: `CREATE DATABASE`. (If it already exists, the error `database "langfuse" already exists` is fine — skip ahead.)

- [ ] **Step 2: Generate the two local-dev secrets**

Run: `docker compose exec postgres sh -c 'echo "NEXTAUTH_SECRET=$(openssl rand -hex 32)"; echo "SALT=$(openssl rand -hex 32)"'`
Expected: two hex strings — paste them into the compose service in the next step. (Local-dev values; fine to keep in compose per the spec.)

- [ ] **Step 3: Add the service to `docker-compose.yml`**

Insert after the `pgadmin` service (before `volumes:`), substituting the generated values:

```yaml
  langfuse:
    image: langfuse/langfuse:2
    depends_on:
      postgres:
        condition: service_healthy
    ports:
      - "3000:3000"
    environment:
      DATABASE_URL: postgresql://postgres:postgres@postgres:5432/langfuse
      NEXTAUTH_URL: http://localhost:3000
      NEXTAUTH_SECRET: <paste generated NEXTAUTH_SECRET>
      SALT: <paste generated SALT>
```

- [ ] **Step 4: Update `.env.example`**

Add a Langfuse block:

```bash
# Langfuse (self-hosted AI observability — UI at http://localhost:3000).
# Create a local account + project there, then paste the project's keys.
# Empty keys disable tracing entirely.
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_HOST=http://langfuse:3000
```

And delete the stale SerpAPI block (the project switched to Serper; `SERPER_API_KEY` already exists under external enrichment APIs):

```bash
# SerpAPI (Google Search — used by tenant onboarding pipeline to discover website pages)
SERPAPI_API_KEY=
```

- [ ] **Step 5: Start Langfuse and verify it is healthy**

Run: `docker compose up -d langfuse`
Then: `docker compose logs langfuse --tail 20`
Expected: migrations applied, server listening on port 3000. Verify with
`curl -s http://localhost:3000/api/public/health` → `{"status":"OK", ...}`.

- [ ] **Step 6: Commit**

```bash
git add docker-compose.yml .env.example
git commit -m "chore: add self-hosted langfuse v2 service to compose stack"
```

---

### Task 8: Full CI + manual end-to-end verification

**Files:** none (verification only; `.env` is edited locally and never committed)

- [ ] **Step 1: Run the full local gate**

Run: `docker compose exec app uv run ruff check . && docker compose exec app uv run mypy . && docker compose exec app uv run pytest`
Expected: ruff clean, mypy clean, all tests pass. Fix anything that fails before proceeding.

- [ ] **Step 2: Create the local Langfuse account and project**

Manual (user): open http://localhost:3000 → Sign up (local account, stored in our own Postgres — no cloud signup) → New project (e.g. `leadengine`) → Settings → API Keys → copy the `pk-lf-…` and `sk-lf-…` keys.

- [ ] **Step 3: Put the keys in `.env` and restart app + worker**

Append to `.env`:

```bash
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=http://langfuse:3000
```

Run: `docker compose restart app worker`

- [ ] **Step 4: Reset onboarding state so a fresh run is possible**

Run: `docker compose exec postgres psql -U postgres -d leadengine -c "UPDATE users SET tenant_id = NULL; DELETE FROM tenant_configs; DELETE FROM tenants;"`
Expected: `UPDATE`/`DELETE` row counts reported without error.

- [ ] **Step 5: Trigger a real onboarding run**

Manual (user): via http://localhost:8000/docs → Authorize → `POST /onboarding` with a real `website_url` (same flow as previous sessions).

Watch it complete: `docker compose logs worker -f`
Expected: job completes, `onboarding_status` reaches `COMPLETE`.

- [ ] **Step 6: Verify the trace in Langfuse**

Manual (user): http://localhost:3000 → Traces. Expected:
- one trace named `tenant-onboarding`, tagged with the tenant's UUID
- three child generations (named after the tool calls: `output_business_profile`, the ICP tool, the signals tool) with prompt, structured output, token counts, and latency
- (if a run fails, its trace is marked errored at the failing step)

- [ ] **Step 7: Final check — everything committed**

Run: `git status --short`
Expected: clean (only `.env` modified locally, which is gitignored).
