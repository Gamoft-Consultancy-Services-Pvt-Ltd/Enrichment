# Persona Web Search Enhancement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single homepage fetch in the onboarding pipeline with a SerpAPI-powered multi-page scrape that discovers and fetches the 5 most relevant pages on a company's website, giving the persona agent richer input.

**Architecture:** A new `clients/serpapi_client.py` wraps the SerpAPI HTTP API; `pipeline.py` calls it to get URLs, fetches them in parallel with `asyncio.gather`, combines the text, and falls back to the homepage if SerpAPI returns nothing or all fetches fail. `persona.py` is unchanged.

**Tech Stack:** httpx (already a dep), asyncio, SerpAPI JSON API (`https://serpapi.com/search.json`), pytest + unittest.mock.

---

## File Map

| Action | File |
|---|---|
| Modify | `core/config.py` — add `serpapi_api_key` field |
| Modify | `.env.example` — add `SERPAPI_API_KEY=` placeholder |
| Create | `clients/serpapi_client.py` — thin httpx wrapper around SerpAPI |
| Create | `tests/unit/test_serpapi_client.py` — unit tests (httpx mocked) |
| Modify | `modules/tenant_onboarding/pipeline.py` — add helpers, rewire fetch logic |
| Modify | `tests/unit/test_onboarding_pipeline.py` — add new helper tests, patch `search_site_pages` |

---

## Task 1: Add `serpapi_api_key` to config

**Files:**
- Modify: `core/config.py`
- Modify: `.env.example`
- Test: `tests/unit/test_config.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_config.py`:

```python
def test_settings_has_serpapi_api_key() -> None:
    from tests.helpers import build_settings
    s = build_settings(serpapi_api_key="test-key")
    assert s.serpapi_api_key == "test-key"


def test_settings_serpapi_api_key_defaults_to_empty() -> None:
    from tests.helpers import build_settings
    s = build_settings()
    assert s.serpapi_api_key == ""
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_config.py::test_settings_has_serpapi_api_key -v
```

Expected: `FAILED — AttributeError: 'Settings' object has no attribute 'serpapi_api_key'`

- [ ] **Step 3: Add the field to `core/config.py`**

In `core/config.py`, after the `groq_api_key` line, add:

```python
serpapi_api_key: str = ""
```

The full `Settings` class should look like:

```python
class Settings(BaseSettings):
    """Strongly-typed application settings, loaded from the environment."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "development"
    log_level: str = "INFO"
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/leadengine"

    redis_url: str = "redis://localhost:6379"
    groq_api_key: str = ""
    serpapi_api_key: str = ""

    # Auth0 (managed auth provider). Empty defaults keep tests/local imports working.
    auth0_domain: str = ""
    auth0_audience: str = ""
    auth0_algorithms: list[str] = ["RS256"]
    auth_claim_namespace: str = "https://leadengine/"
    auth0_spa_client_id: str = ""
```

- [ ] **Step 4: Add placeholder to `.env.example`**

After the `GROQ_API_KEY=` line, add:

```
# SerpAPI (Google Search — used by tenant onboarding pipeline to discover website pages)
SERPAPI_API_KEY=
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_config.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add core/config.py .env.example tests/unit/test_config.py
git commit -m "feat: add serpapi_api_key to Settings"
```

---

## Task 2: Create `clients/serpapi_client.py`

**Files:**
- Create: `clients/serpapi_client.py`
- Create: `tests/unit/test_serpapi_client.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_serpapi_client.py`:

```python
"""Unit tests for clients/serpapi_client — mocks httpx."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clients.serpapi_client import search_site_pages
from core.exceptions import ExternalServiceError


def _make_response(status: int, body: dict[str, Any]) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body
    return r


async def test_returns_urls_from_organic_results() -> None:
    links = [f"https://example.com/page{i}" for i in range(5)]
    body = {"organic_results": [{"link": l} for l in links]}
    mock_resp = _make_response(200, body)

    with patch("clients.serpapi_client.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_resp)
        urls = await search_site_pages("example.com", num=5)

    assert urls == links


async def test_respects_num_limit() -> None:
    links = [f"https://example.com/page{i}" for i in range(10)]
    body = {"organic_results": [{"link": l} for l in links]}
    mock_resp = _make_response(200, body)

    with patch("clients.serpapi_client.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_resp)
        urls = await search_site_pages("example.com", num=3)

    assert len(urls) == 3


async def test_returns_empty_list_when_no_organic_results() -> None:
    mock_resp = _make_response(200, {"organic_results": []})

    with patch("clients.serpapi_client.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_resp)
        urls = await search_site_pages("example.com")

    assert urls == []


async def test_returns_empty_list_when_key_missing_from_response() -> None:
    mock_resp = _make_response(200, {})

    with patch("clients.serpapi_client.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_resp)
        urls = await search_site_pages("example.com")

    assert urls == []


async def test_raises_external_service_error_on_non_200() -> None:
    mock_resp = _make_response(401, {})

    with patch("clients.serpapi_client.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_resp)
        with pytest.raises(ExternalServiceError, match="SerpAPI returned 401"):
            await search_site_pages("example.com")


async def test_raises_external_service_error_on_network_failure() -> None:
    with patch("clients.serpapi_client.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__.return_value.get = AsyncMock(
            side_effect=Exception("connection refused")
        )
        with pytest.raises(ExternalServiceError, match="SerpAPI request failed"):
            await search_site_pages("example.com")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_serpapi_client.py -v
```

Expected: `FAILED — ModuleNotFoundError: No module named 'clients.serpapi_client'` (or similar import error).

- [ ] **Step 3: Implement `clients/serpapi_client.py`**

```python
"""Thin async wrapper around the SerpAPI Google Search JSON API."""

from typing import Any

import httpx

from core.config import get_settings
from core.exceptions import ExternalServiceError

_SERPAPI_URL = "https://serpapi.com/search.json"


async def search_site_pages(domain: str, num: int = 5) -> list[str]:
    """Return up to `num` page URLs for `site:<domain>` from SerpAPI.

    Returns an empty list if SerpAPI has no organic results.
    Raises ExternalServiceError on HTTP errors or network failures.
    """
    params: dict[str, Any] = {
        "q": f"site:{domain}",
        "num": num,
        "api_key": get_settings().serpapi_api_key,
        "engine": "google",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            response = await http.get(_SERPAPI_URL, params=params)
    except Exception as exc:
        raise ExternalServiceError(f"SerpAPI request failed: {exc}") from exc

    if response.status_code != 200:
        raise ExternalServiceError(f"SerpAPI returned {response.status_code}")

    data: dict[str, Any] = response.json()
    results: list[dict[str, Any]] = data.get("organic_results", [])
    return [r["link"] for r in results[:num]]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_serpapi_client.py -v
```

Expected: all 6 tests pass.

- [ ] **Step 5: Run lint and typecheck**

```bash
uv run ruff check clients/serpapi_client.py && uv run mypy clients/serpapi_client.py
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add clients/serpapi_client.py tests/unit/test_serpapi_client.py
git commit -m "feat: add serpapi_client with search_site_pages"
```

---

## Task 3: Update `pipeline.py` — add helpers and rewire fetch logic

**Files:**
- Modify: `modules/tenant_onboarding/pipeline.py`
- Modify: `tests/unit/test_onboarding_pipeline.py`

- [ ] **Step 1: Write failing tests for the new helpers**

First, add `import httpx` to the imports at the top of `tests/unit/test_onboarding_pipeline.py` (it already has `from unittest.mock import AsyncMock, MagicMock, patch` — just add the httpx import alongside the existing ones).

Also extend the existing import from pipeline:
```python
from modules.tenant_onboarding.pipeline import _combine_page_texts, _extract_domain, _strip_html, run_pipeline
```

Then add these new test functions at the bottom of the file:

```python
def test_extract_domain_strips_scheme_and_path() -> None:
    assert _extract_domain("https://acme.com/about") == "acme.com"


def test_extract_domain_handles_bare_domain() -> None:
    assert _extract_domain("https://acme.com") == "acme.com"


def test_combine_page_texts_joins_successful_responses() -> None:
    def make_resp(status: int, text: str) -> MagicMock:
        r = MagicMock(spec=httpx.Response)
        r.status_code = status
        r.text = text
        return r

    results: list[httpx.Response | BaseException] = [
        make_resp(200, "<p>About us</p>"),
        make_resp(200, "<p>Products</p>"),
        make_resp(404, "<p>Not found</p>"),
        ValueError("timeout"),
    ]
    combined = _combine_page_texts(results)
    assert "About us" in combined
    assert "Products" in combined
    assert "Not found" not in combined


def test_combine_page_texts_returns_empty_when_all_fail() -> None:
    results: list[httpx.Response | BaseException] = [
        ValueError("timeout"),
        RuntimeError("refused"),
    ]
    assert _combine_page_texts(results) == ""
```

- [ ] **Step 2: Run to verify they fail**

```bash
uv run pytest tests/unit/test_onboarding_pipeline.py::test_extract_domain_strips_scheme_and_path -v
```

Expected: `FAILED — ImportError: cannot import name '_extract_domain'`

- [ ] **Step 3: Rewrite `modules/tenant_onboarding/pipeline.py`**

Replace the entire file with:

```python
"""Pipeline: fetches the tenant's website and runs the three agents in sequence."""

import asyncio
import re
from urllib.parse import urlparse
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from clients.serpapi_client import search_site_pages
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
            domain = _extract_domain(str(tenant.website_url))
            urls = await search_site_pages(domain, num=5)
            if urls:
                gather_results = await asyncio.gather(
                    *[http.get(u) for u in urls], return_exceptions=True
                )
                page_results: list[httpx.Response | BaseException] = list(gather_results)  # type: ignore[arg-type]
                website_text = _combine_page_texts(page_results)
            else:
                website_text = ""
            if not website_text:
                website_text = await _fallback_fetch(http, str(tenant.website_url))

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


def _extract_domain(url: str) -> str:
    """Return the bare hostname from a URL ('https://acme.com/x' → 'acme.com')."""
    return urlparse(url).netloc


def _combine_page_texts(results: list[httpx.Response | BaseException]) -> str:
    """Strip HTML from successful responses and join with double newlines."""
    parts = []
    for r in results:
        if isinstance(r, BaseException):
            continue
        if r.status_code != 200:
            continue
        parts.append(_strip_html(r.text))
    return "\n\n".join(parts)


async def _fallback_fetch(http: httpx.AsyncClient, url: str) -> str:
    """Fetch url directly and return stripped plain text."""
    response = await http.get(url)
    response.raise_for_status()
    return _strip_html(response.text)


def _strip_html(html: str) -> str:
    """Extract readable text from HTML without external dependencies."""
    text = re.sub(
        r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE
    )
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()
```

- [ ] **Step 4: Update existing pipeline tests to patch `search_site_pages`**

Replace `test_pipeline_happy_path_sets_complete` with:

```python
async def test_pipeline_happy_path_sets_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant_id = uuid4()
    mock_session = AsyncMock()

    business_profile = {"industry": "SaaS", "target_market": "SMB"}
    icp_data = {"buyer_role": "VP Sales", "company_size": "50-200"}
    from shared.tenant_config.schemas import Dimension, Signal, Thresholds, Weights

    sigs = [Signal(id=f"{d.value.lower()}_1", dimension=d, question="?") for d in Dimension]
    weights = Weights(fit=0.2, intent=0.2, engagement=0.2, behaviour=0.2, context=0.2)
    thresholds = Thresholds(hot=80, warm=55)

    set_status_mock = AsyncMock()
    monkeypatch.setattr(
        "modules.tenant_onboarding.pipeline.get_tenant",
        AsyncMock(return_value=_mock_tenant(tenant_id)),
    )
    monkeypatch.setattr(
        "modules.tenant_onboarding.pipeline.set_onboarding_status", set_status_mock
    )
    monkeypatch.setattr(
        "modules.tenant_onboarding.pipeline.search_site_pages",
        AsyncMock(return_value=[]),  # empty → triggers fallback to homepage
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

    with patch("modules.tenant_onboarding.pipeline.httpx.AsyncClient") as mock_http:
        mock_http.return_value.__aenter__.return_value.get = AsyncMock(
            return_value=mock_http_response
        )
        await run_pipeline(mock_session, tenant_id)

    calls = [c.args[2] for c in set_status_mock.call_args_list]
    assert OnboardingStatus.RUNNING in calls
    assert OnboardingStatus.COMPLETE in calls
```

Replace `test_pipeline_sets_failed_on_exception` with:

```python
async def test_pipeline_sets_failed_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant_id = uuid4()
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
        "modules.tenant_onboarding.pipeline.search_site_pages",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "modules.tenant_onboarding.pipeline.persona.run",
        AsyncMock(side_effect=Exception("network timeout")),
    )

    with patch("modules.tenant_onboarding.pipeline.httpx.AsyncClient") as mock_http:
        mock_http_response = MagicMock()
        mock_http_response.status_code = 200
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

Also add one test for the SerpAPI happy path (pages found, no fallback needed):

```python
async def test_pipeline_uses_serpapi_pages_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
        AsyncMock(return_value=["https://acme.com/about", "https://acme.com/products"]),
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

    mock_page = MagicMock()
    mock_page.status_code = 200
    mock_page.text = "<html><body>Acme page content</body></html>"

    with patch("modules.tenant_onboarding.pipeline.httpx.AsyncClient") as mock_http:
        mock_http.return_value.__aenter__.return_value.get = AsyncMock(
            return_value=mock_page
        )
        await run_pipeline(mock_session, tenant_id)
```

- [ ] **Step 5: Run all pipeline and helper tests**

```bash
uv run pytest tests/unit/test_onboarding_pipeline.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Run the full unit suite**

```bash
uv run pytest tests/unit/ -v
```

Expected: all pass.

- [ ] **Step 7: Run lint and typecheck**

```bash
uv run ruff check modules/tenant_onboarding/pipeline.py && uv run mypy modules/tenant_onboarding/pipeline.py
```

Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add modules/tenant_onboarding/pipeline.py tests/unit/test_onboarding_pipeline.py
git commit -m "feat: wire SerpAPI multi-page scrape into onboarding pipeline"
```

---

## Task 4: Run `make ci` and verify end-to-end

- [ ] **Step 1: Run the full CI gate**

```bash
make ci
```

Expected: lint + typecheck + tests all pass.

- [ ] **Step 2: Add your SerpAPI key to `.env`**

Open `.env` and set:
```
SERPAPI_API_KEY=<your-key-from-serpapi.com>
```

- [ ] **Step 3: Restart the worker to pick up the new env var**

```bash
docker compose up -d --force-recreate worker
docker compose logs -f worker
```

Expected: worker starts cleanly.

- [ ] **Step 4: Trigger a new onboarding via `POST /onboarding`**

Hit the endpoint (via `/docs` at http://localhost:8000/docs or curl) with a valid JWT and a `website_url`. Watch the worker logs — you should see it call SerpAPI, fetch multiple pages, and complete.

- [ ] **Step 5: Verify richer tenant_config**

```bash
docker compose exec postgres psql -U postgres -d leadengine \
  -c "SELECT business_profile FROM tenant_configs ORDER BY created_at DESC LIMIT 1;"
```

The `business_profile` JSON should be more detailed than before (more fields populated, richer descriptions).

- [ ] **Step 6: Final commit if any fixups were needed**

```bash
git add -p
git commit -m "chore: fix any issues found during e2e verification"
```
