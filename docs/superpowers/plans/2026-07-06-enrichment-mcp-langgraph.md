# Enrichment Agent (LangGraph + MCP) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reusable LangGraph web-research agent whose `web_search` tool is served by a standalone Serper MCP server, wire it into lead enrichment (with a deterministic, PII-free Shopify order-history fetch) and cut tenant onboarding over to the same agent.

**Architecture:** A standalone MCP server (streamable-http) exposes `web_search` backed by Serper. A generic `research(goal, output_schema)` agent in `shared/research` connects to that server, runs LangGraph's `create_react_agent` with native `response_format`, and returns a structured result. `modules/enrichment` and `modules/tenant_onboarding` both call `research()`. Shopify order history is fetched deterministically (never by the LLM) and attached to the enrichment result. Cross-module data uses fattened `shared/events` contracts.

**Tech Stack:** Python 3.13, async, LangGraph (`create_react_agent`), `langchain-groq` (`ChatGroq`, `llama-3.3-70b-versatile`, temperature 0), `langchain-mcp-adapters`, `mcp` (FastMCP), Serper, httpx, pydantic v2, Langfuse (callback handler), pytest (`asyncio_mode=auto`), mypy strict, ruff (line length 100).

## Global Constraints

- Dependency rule: `mcp_servers/ → clients`; `shared/research` imports only `clients` + `core` + third-party, never a module; both consuming modules import `shared/research`. No module imports another module.
- TDD: write the failing test, watch it fail for the right reason, minimal code, then refactor. Commit after each green task.
- Unit tests must not need a DB or network — mock `httpx` via `patch("<module>.httpx.AsyncClient")`; mock LangGraph/MCP by patching `create_react_agent`, `MultiServerMCPClient`, and `_langfuse_handler` in `shared.research.agent`; mock Groq SDK via `patch("clients.groq_client.AsyncGroq")`.
- `mypy .` runs strict over the whole repo including `tests/` (`ignore_missing_imports = true`, `disallow_untyped_decorators = false` are already set). `ruff` line length 100 (E501 ignored).
- Groq model constant is `llama-3.3-70b-versatile` (`clients.groq_client._MODEL`); temperature 0 everywhere.
- The Shopify fetch **output must never contain customer email, phone, name, or address** — order facts only.
- External failures raise `core.exceptions.ExternalServiceError`.
- Run the local gate before finishing: `make ci` (lint + typecheck + test).

---

## Task 1: Serper query search (`clients/serper_client.py`)

Add a query-based search returning an LLM-readable text digest. The existing `search_site_pages` stays untouched. No new dependencies.

**Files:**
- Modify: `clients/serper_client.py`
- Test: `tests/unit/test_serper_client.py`

**Interfaces:**
- Produces: `async def search(query: str, num: int = 5) -> str` — raises `ExternalServiceError` on HTTP/network failure.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_serper_client.py` and change the import line at the top to `from clients.serper_client import search, search_site_pages`:

```python
async def test_search_digests_knowledge_graph_and_organic() -> None:
    body = {
        "knowledgeGraph": {
            "title": "Acme Inc",
            "description": "Acme makes CRM software.",
            "attributes": {"Founded": "2010", "HQ": "Pune"},
        },
        "organic": [
            {"title": "Acme raises Series B", "snippet": "Acme raised $20M.", "link": "https://news/1"},
            {"title": "Acme careers", "snippet": "We are hiring.", "link": "https://acme.com/jobs"},
        ],
    }
    mock_resp = _make_response(200, body)
    with patch("clients.serper_client.httpx.AsyncClient") as mock_cls:
        mock_post = AsyncMock(return_value=mock_resp)
        mock_cls.return_value.__aenter__.return_value.post = mock_post
        digest = await search("Acme Inc company", num=5)

    assert "Acme Inc" in digest
    assert "CRM software" in digest
    assert "Founded: 2010" in digest
    assert "Acme raised $20M." in digest
    assert "https://news/1" in digest
    called_json = mock_post.call_args.kwargs.get("json", {})
    assert called_json.get("q") == "Acme Inc company"
    assert called_json.get("num") == 5


async def test_search_returns_empty_string_when_no_results() -> None:
    mock_resp = _make_response(200, {})
    with patch("clients.serper_client.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_resp)
        assert await search("nothing here") == ""


async def test_search_raises_on_non_200() -> None:
    mock_resp = _make_response(500, {})
    with patch("clients.serper_client.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_resp)
        with pytest.raises(ExternalServiceError, match="Serper returned 500"):
            await search("boom")


async def test_search_raises_on_network_failure() -> None:
    with patch("clients.serper_client.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__.return_value.post = AsyncMock(
            side_effect=Exception("connection refused")
        )
        with pytest.raises(ExternalServiceError, match="Serper request failed"):
            await search("boom")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_serper_client.py -k search -v`
Expected: FAIL with `ImportError: cannot import name 'search'`.

- [ ] **Step 3: Implement `search` + `_digest`**

Append to `clients/serper_client.py`:

```python
async def search(query: str, num: int = 5) -> str:
    """Run a web search and return a compact text digest for an LLM to read.

    Combines the knowledge graph (if any) with the top organic snippets. Returns
    an empty string when Serper has nothing. Raises ExternalServiceError on HTTP
    errors or network failures.
    """
    headers = {
        "X-API-KEY": get_settings().serper_api_key,
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {"q": query, "num": num}
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            response = await http.post(_SERPER_URL, headers=headers, json=payload)
    except Exception as exc:
        raise ExternalServiceError(f"Serper request failed: {exc}") from exc

    if response.status_code != 200:
        raise ExternalServiceError(f"Serper returned {response.status_code}")

    return _digest(response.json())


def _digest(data: dict[str, Any]) -> str:
    """Flatten a Serper response into readable lines."""
    parts: list[str] = []
    kg = data.get("knowledgeGraph") or {}
    if kg:
        headline = f"{kg.get('title', '')}: {kg.get('description', '')}".strip(": ").strip()
        if headline:
            parts.append(headline)
        for key, value in (kg.get("attributes") or {}).items():
            parts.append(f"{key}: {value}")
    for result in data.get("organic", []):
        title = result.get("title", "")
        snippet = result.get("snippet", "")
        link = result.get("link", "")
        line = f"{title} — {snippet} ({link})".strip(" —()")
        if line:
            parts.append(line)
    return "\n".join(parts)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_serper_client.py -v`
Expected: PASS (old and new).

- [ ] **Step 5: Commit**

```bash
git add clients/serper_client.py tests/unit/test_serper_client.py
git commit -m "feat: add query-based Serper search returning an LLM digest"
```

---

## Task 2: Dependencies + MCP URL config (`pyproject.toml`, `core/config.py`)

Add the LangGraph/MCP libraries and a settings field for the MCP server URL.

**Files:**
- Modify: `pyproject.toml`
- Modify: `core/config.py`
- Test: `tests/unit/test_config.py`

**Interfaces:**
- Produces: `Settings.mcp_web_search_url: str` (default `http://localhost:8000/mcp`).

- [ ] **Step 1: Add dependencies**

Run:
```bash
uv add langgraph langchain-groq langchain-mcp-adapters mcp
```
Expected: `pyproject.toml` `dependencies` gains the four packages and `uv.lock` updates. (`langfuse` is already present.)

- [ ] **Step 2: Write the failing test**

Append to `tests/unit/test_config.py`:

```python
def test_mcp_web_search_url_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_WEB_SEARCH_URL", raising=False)
    settings = build_settings()
    assert settings.mcp_web_search_url == "http://localhost:8000/mcp"


def test_mcp_web_search_url_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_WEB_SEARCH_URL", "http://mcp-web-search:8000/mcp")
    settings = build_settings()
    assert settings.mcp_web_search_url == "http://mcp-web-search:8000/mcp"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_config.py -k mcp_web_search_url -v`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'mcp_web_search_url'`.

- [ ] **Step 4: Add the setting**

In `core/config.py`, add beside `serper_api_key`:

```python
    mcp_web_search_url: str = "http://localhost:8000/mcp"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_config.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock core/config.py tests/unit/test_config.py
git commit -m "chore: add langgraph/mcp deps and mcp_web_search_url setting"
```

---

## Task 3: Groq chat-model factory (`clients/groq_client.py`)

Add a `ChatGroq` factory beside the untouched `call_with_tool`.

**Files:**
- Modify: `clients/groq_client.py`
- Test: `tests/unit/test_groq_client.py`

**Interfaces:**
- Consumes: `get_settings` (already imported), `_MODEL` (already defined).
- Produces: `def get_chat_model() -> ChatGroq`.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_groq_client.py`:

```python
def test_get_chat_model_configures_groq(monkeypatch: pytest.MonkeyPatch) -> None:
    from clients import groq_client
    from tests.helpers import build_settings

    monkeypatch.setattr(
        groq_client, "get_settings", lambda: build_settings(groq_api_key="test-key")
    )
    model = groq_client.get_chat_model()
    assert model.model_name == "llama-3.3-70b-versatile"
    assert model.temperature == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_groq_client.py -k get_chat_model -v`
Expected: FAIL with `AttributeError: module 'clients.groq_client' has no attribute 'get_chat_model'`.

- [ ] **Step 3: Implement the factory**

In `clients/groq_client.py`, add the import near the top:

```python
from langchain_groq import ChatGroq
```

and add the function:

```python
def get_chat_model() -> ChatGroq:
    """Return a configured ChatGroq for LangGraph agents (temperature 0)."""
    return ChatGroq(model=_MODEL, temperature=0.0, api_key=get_settings().groq_api_key)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_groq_client.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add clients/groq_client.py tests/unit/test_groq_client.py
git commit -m "feat: add ChatGroq factory for LangGraph agents"
```

---

## Task 4: MCP web_search server (`mcp_servers/web_search/server.py`)

A standalone FastMCP server exposing one tool, `web_search`, backed by `serper_client.search`.

**Files:**
- Create: `mcp_servers/__init__.py` (empty)
- Create: `mcp_servers/web_search/__init__.py` (empty)
- Create: `mcp_servers/web_search/server.py`
- Test: `tests/unit/test_mcp_web_search_server.py`

**Interfaces:**
- Consumes: `clients.serper_client.search`.
- Produces: `async def web_search(query: str, num: int = 5) -> str`; module-level `mcp: FastMCP`; `def main() -> None`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_mcp_web_search_server.py`:

```python
"""Unit tests for the web_search MCP server — mocks serper."""

from unittest.mock import AsyncMock, patch


async def test_web_search_delegates_to_serper() -> None:
    with patch(
        "mcp_servers.web_search.server.serper_client.search",
        AsyncMock(return_value="a readable digest"),
    ) as mock_search:
        from mcp_servers.web_search.server import web_search

        out = await web_search("acme crm", 3)

    assert out == "a readable digest"
    mock_search.assert_awaited_once_with("acme crm", num=3)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_mcp_web_search_server.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_servers'`.

- [ ] **Step 3: Implement the server**

Create `mcp_servers/__init__.py` and `mcp_servers/web_search/__init__.py` (both empty).

Create `mcp_servers/web_search/server.py`:

```python
"""Standalone MCP server exposing a single `web_search` tool backed by Serper.

Runs over streamable-http. The Serper API key lives only in this process's env;
the research agent connects by URL and never sees the key.
"""

from mcp.server.fastmcp import FastMCP

from clients import serper_client

mcp = FastMCP("web-search")


@mcp.tool()
async def web_search(query: str, num: int = 5) -> str:
    """Search the web for `query` and return a readable digest of the top results.

    Use this to research a company, person, or topic. Compose a focused query;
    call again with a refined query if the first result is not enough.
    """
    return await serper_client.search(query, num=num)


def main() -> None:
    """Run the MCP server over streamable-http (host/port from FastMCP settings)."""
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_mcp_web_search_server.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mcp_servers/ tests/unit/test_mcp_web_search_server.py
git commit -m "feat: add standalone Serper web_search MCP server"
```

---

## Task 5: Research agent (`shared/research/agent.py`)

The reusable agent: connect to the MCP server, run `create_react_agent` with native `response_format`, return the structured result.

**Files:**
- Create: `shared/research/__init__.py` (empty)
- Create: `shared/research/agent.py`
- Test: `tests/unit/test_research_agent.py`

**Interfaces:**
- Consumes: `clients.groq_client.get_chat_model`, `core.config.get_settings`, `core.exceptions.ExternalServiceError`, `create_react_agent`, `MultiServerMCPClient`, `CallbackHandler`.
- Produces: `async def research(*, goal: str, output_schema: type[T]) -> T` where `T = TypeVar("T", bound=BaseModel)`. Raises `ExternalServiceError` on failure.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_research_agent.py`:

```python
"""Unit tests for shared/research/agent — mocks LangGraph + MCP."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

from core.exceptions import ExternalServiceError
from shared.research.agent import research


class _Findings(BaseModel):
    summary: str = ""
    confidence: float = 0.0


def _patch_agent(structured: Any) -> Any:
    """Return a patched create_react_agent whose graph returns `structured`."""
    fake_graph = MagicMock()
    fake_graph.ainvoke = AsyncMock(return_value={"structured_response": structured})
    return MagicMock(return_value=fake_graph), fake_graph


async def test_research_returns_structured_response() -> None:
    result = _Findings(summary="Acme makes CRM", confidence=0.9)
    make_agent, fake_graph = _patch_agent(result)

    with (
        patch("shared.research.agent.create_react_agent", make_agent),
        patch("shared.research.agent.MultiServerMCPClient") as mock_client_cls,
        patch("shared.research.agent.get_chat_model", MagicMock()),
        patch("shared.research.agent._langfuse_handler", MagicMock(return_value=MagicMock())),
    ):
        mock_client_cls.return_value.get_tools = AsyncMock(return_value=[MagicMock()])
        out = await research(goal="Research Acme Inc", output_schema=_Findings)

    assert out is result
    # goal reached the graph
    sent = fake_graph.ainvoke.call_args.args[0]
    assert any("Acme" in getattr(m, "content", "") for m in sent["messages"])
    # response_format wired to our schema
    assert make_agent.call_args.kwargs["response_format"] is _Findings


async def test_research_wraps_failures() -> None:
    make_agent, fake_graph = _patch_agent(None)
    fake_graph.ainvoke = AsyncMock(side_effect=Exception("mcp down"))

    with (
        patch("shared.research.agent.create_react_agent", make_agent),
        patch("shared.research.agent.MultiServerMCPClient") as mock_client_cls,
        patch("shared.research.agent.get_chat_model", MagicMock()),
        patch("shared.research.agent._langfuse_handler", MagicMock(return_value=MagicMock())),
    ):
        mock_client_cls.return_value.get_tools = AsyncMock(return_value=[MagicMock()])
        with pytest.raises(ExternalServiceError, match="research agent failed"):
            await research(goal="boom", output_schema=_Findings)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_research_agent.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shared.research'`.

- [ ] **Step 3: Implement the agent**

Create `shared/research/__init__.py` (empty) and `shared/research/agent.py`:

```python
"""Reusable web-research agent (LangGraph) sharing one MCP-hosted web_search tool.

`research(goal, output_schema)` is generic: callers pass a goal string and a
pydantic output schema. It knows nothing about enrichment or onboarding.
"""

from typing import TypeVar, cast

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langfuse.callback import CallbackHandler
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel

from clients.groq_client import get_chat_model
from core.config import get_settings
from core.exceptions import ExternalServiceError

T = TypeVar("T", bound=BaseModel)

_RECURSION_LIMIT = 12

_SYSTEM = (
    "You are a research agent. Use the web_search tool to gather the facts needed "
    "to fill in the requested structured output. Compose focused queries, read the "
    "results, and search again if you are not yet confident. Stop as soon as you "
    "have enough. Base every field only on what you found; do not invent facts."
)


def _langfuse_handler() -> CallbackHandler:
    """Return a Langfuse callback handler (isolated for test patching)."""
    return CallbackHandler()


async def research(*, goal: str, output_schema: type[T]) -> T:
    """Run the web-research agent toward `goal` and return `output_schema` filled in.

    Raises ExternalServiceError if the MCP server is unreachable or the agent fails.
    """
    try:
        client = MultiServerMCPClient(
            {
                "web_search": {
                    "url": get_settings().mcp_web_search_url,
                    "transport": "streamable_http",
                }
            }
        )
        tools = await client.get_tools()
        agent = create_react_agent(get_chat_model(), tools, response_format=output_schema)
        state = await agent.ainvoke(
            {"messages": [SystemMessage(content=_SYSTEM), HumanMessage(content=goal)]},
            config={"recursion_limit": _RECURSION_LIMIT, "callbacks": [_langfuse_handler()]},
        )
    except Exception as exc:
        raise ExternalServiceError(f"research agent failed: {exc}") from exc

    return cast(T, state["structured_response"])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_research_agent.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add shared/research/ tests/unit/test_research_agent.py
git commit -m "feat: add reusable LangGraph research agent over MCP web_search"
```

---

## Task 6: Fatten event contracts + wire lead_ingestion (`shared/events/schemas.py`)

Add `LeadPayload` and `EnrichmentResult`; make `LeadReceived.payload` required and `LeadEnriched.result` required; populate the payload at lead_ingestion's one emission site (Option A).

**Files:**
- Modify: `shared/events/schemas.py`
- Modify: `modules/lead_ingestion/pipeline.py`
- Modify: `modules/lead_ingestion/schemas/normalised_event.py` (remove a stray blank line)
- Test: `tests/unit/test_event_schemas.py`

**Interfaces:**
- Produces: `class LeadPayload(BaseModel)`, `class EnrichmentResult(BaseModel)`, `LeadReceived.payload: LeadPayload`, `LeadEnriched.result: EnrichmentResult`.

- [ ] **Step 1: Update the tests**

In `tests/unit/test_event_schemas.py`, add `EnrichmentResult` and `LeadPayload` to the import from `shared.events.schemas`. Replace the existing `LeadReceived`/`LeadEnriched` happy-path tests with:

```python
def test_lead_received_carries_lead_id_source_and_payload() -> None:
    tenant_id, lead_id = uuid4(), uuid4()
    payload = LeadPayload(email="a@b.com", source=LeadSource.EMAIL)
    evt = LeadReceived(
        tenant_id=tenant_id, lead_id=lead_id, source=LeadSource.EMAIL, payload=payload
    )
    assert evt.event_type == "LeadReceived"
    assert evt.lead_id == lead_id
    assert evt.source is LeadSource.EMAIL
    assert evt.payload.email == "a@b.com"


def test_lead_received_requires_payload() -> None:
    with pytest.raises(ValidationError):
        LeadReceived.model_validate(
            {"tenant_id": str(uuid4()), "lead_id": str(uuid4()), "source": "EMAIL"}
        )


def test_lead_enriched_carries_lead_id_and_result() -> None:
    tenant_id, lead_id = uuid4(), uuid4()
    result = EnrichmentResult(confidence=0.8)
    evt = LeadEnriched(tenant_id=tenant_id, lead_id=lead_id, result=result)
    assert evt.event_type == "LeadEnriched"
    assert evt.lead_id == lead_id
    assert evt.result.confidence == 0.8


def test_enrichment_result_rejects_out_of_range_confidence() -> None:
    with pytest.raises(ValidationError):
        EnrichmentResult(confidence=1.5)
```

(Leave any `..._requires_lead_id_and_source` / `..._rejects_invalid_source` tests as-is.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_event_schemas.py -v`
Expected: FAIL with `ImportError: cannot import name 'LeadPayload'`.

- [ ] **Step 3: Implement the schema changes**

In `shared/events/schemas.py`, change the typing import to `from typing import Any, Literal`. Add two models above `class Event`:

```python
class LeadPayload(BaseModel):
    """Normalized lead data carried on LeadReceived (event-carried state).

    Enrichment reads this off the event and never touches lead_ingestion's tables.
    """

    model_config = ConfigDict(frozen=True)

    name: str | None = None
    email: str | None = None
    phone: str | None = None
    company: str | None = None
    source: LeadSource
    first_party: dict[str, Any] = Field(default_factory=dict)


class EnrichmentResult(BaseModel):
    """Structured enrichment output carried on LeadEnriched, consumed by scoring."""

    model_config = ConfigDict(frozen=True)

    company_info: dict[str, Any] = Field(default_factory=dict)
    person_info: dict[str, Any] = Field(default_factory=dict)
    order_history: dict[str, Any] | None = None
    sources: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    reasoning_trace: str = ""
```

Replace the `LeadReceived` and `LeadEnriched` classes:

```python
class LeadReceived(Event):
    """lead_ingestion accepted a genuine lead and persisted it; not yet scored."""

    event_type: Literal["LeadReceived"] = "LeadReceived"
    lead_id: UUID
    source: LeadSource
    payload: LeadPayload


class LeadEnriched(Event):
    """enrichment finished gathering data for a lead; ready to score."""

    event_type: Literal["LeadEnriched"] = "LeadEnriched"
    lead_id: UUID
    result: EnrichmentResult
```

- [ ] **Step 4: Run event tests to verify they pass**

Run: `uv run pytest tests/unit/test_event_schemas.py -v`
Expected: PASS.

- [ ] **Step 5: Wire lead_ingestion to populate the payload**

In `modules/lead_ingestion/pipeline.py`, change the import to `from shared.events.schemas import LeadPayload, LeadReceived` and replace the `LeadReceived(...)` construction:

```python
    return (
        lead,
        LeadReceived(
            tenant_id=event.tenant_id,
            lead_id=lead.id,
            source=event.source,
            payload=LeadPayload(
                name=event.full_name,
                email=event.email,
                phone=event.phone,
                source=event.source,
                first_party=event.extra_fields,
            ),
        ),
    )
```

In `modules/lead_ingestion/schemas/normalised_event.py`, remove the stray blank line after `source: LeadSource`.

- [ ] **Step 6: Run the lead_ingestion unit tests**

Run: `uv run pytest tests/unit -k lead_ingestion -v` and `uv run pytest tests/unit/test_event_schemas.py -v`
Expected: PASS. (Integration tests assert only `lead_id`/`tenant_id`/`source` and drive the real pipeline, so they remain valid.)

- [ ] **Step 7: Commit**

```bash
git add shared/events/schemas.py tests/unit/test_event_schemas.py modules/lead_ingestion/pipeline.py modules/lead_ingestion/schemas/normalised_event.py
git commit -m "feat: fatten LeadReceived/LeadEnriched and populate lead payload"
```

---

## Task 7: Enrichment schemas (`modules/enrichment/schemas.py`)

Define the agent output schema, the Shopify creds/deps, and re-export the contract.

**Files:**
- Create: `modules/enrichment/schemas.py`
- Test: `tests/unit/test_enrichment_schemas.py`

**Interfaces:**
- Consumes: `shared.events.schemas.LeadPayload`, `EnrichmentResult`.
- Produces: `class ResearchFindings(BaseModel)` (company_info, person_info, sources, confidence, reasoning_trace); `class ShopifyCreds(BaseModel)` (shop_domain, access_token); `class EnrichmentDeps(BaseModel)` (shopify_creds: ShopifyCreds | None = None); `EnrichmentInput = LeadPayload`; re-export `EnrichmentResult`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_enrichment_schemas.py`:

```python
"""Unit tests for modules/enrichment/schemas."""

import pytest
from pydantic import ValidationError

from modules.enrichment.schemas import (
    EnrichmentDeps,
    EnrichmentInput,
    EnrichmentResult,
    ResearchFindings,
    ShopifyCreds,
)
from shared.events.schemas import LeadPayload


def test_enrichment_input_is_lead_payload() -> None:
    assert EnrichmentInput is LeadPayload


def test_research_findings_defaults() -> None:
    f = ResearchFindings(confidence=0.5)
    assert f.company_info == {}
    assert f.sources == []


def test_research_findings_rejects_bad_confidence() -> None:
    with pytest.raises(ValidationError):
        ResearchFindings(confidence=2.0)


def test_findings_map_onto_enrichment_result() -> None:
    f = ResearchFindings(confidence=0.7, sources=["https://x"])
    result = EnrichmentResult(**f.model_dump(), order_history=None)
    assert result.confidence == 0.7
    assert result.order_history is None


def test_enrichment_deps_defaults_to_no_shopify() -> None:
    assert EnrichmentDeps().shopify_creds is None
    creds = ShopifyCreds(shop_domain="acme.myshopify.com", access_token="tok")
    assert EnrichmentDeps(shopify_creds=creds).shopify_creds is creds
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_enrichment_schemas.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'modules.enrichment.schemas'`.

- [ ] **Step 3: Implement the schemas**

Create `modules/enrichment/schemas.py`:

```python
"""Enrichment schemas: the agent's output, Shopify deps, and the shared contract.

ResearchFindings is the agent's response_format output. EnrichmentResult (the
LeadEnriched payload) is ResearchFindings plus the deterministically-fetched
order_history. Both the input (LeadPayload) and result are defined once in
shared/events; this module re-exports them so callers import from one place.
"""

from typing import Any

from pydantic import BaseModel, Field

from shared.events.schemas import EnrichmentResult, LeadPayload

EnrichmentInput = LeadPayload

__all__ = [
    "EnrichmentDeps",
    "EnrichmentInput",
    "EnrichmentResult",
    "ResearchFindings",
    "ShopifyCreds",
]


class ResearchFindings(BaseModel):
    """The web-research half of an enrichment — the agent's structured output."""

    company_info: dict[str, Any] = Field(default_factory=dict)
    person_info: dict[str, Any] = Field(default_factory=dict)
    sources: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    reasoning_trace: str = ""


class ShopifyCreds(BaseModel):
    """A tenant's Shopify Admin API credentials (never sent to the LLM)."""

    shop_domain: str
    access_token: str


class EnrichmentDeps(BaseModel):
    """Runtime dependencies injected per enrichment run by the (future) caller."""

    shopify_creds: ShopifyCreds | None = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_enrichment_schemas.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add modules/enrichment/schemas.py tests/unit/test_enrichment_schemas.py
git commit -m "feat: add enrichment schemas (findings, shopify creds, deps)"
```

---

## Task 8: Shopify order-history fetch (`modules/enrichment/tools/shopify_order_history.py`)

The deterministic, PII-free order-history fetcher. Real Shopify call deferred (stub raises `NotImplementedError`).

**Files:**
- Create: `modules/enrichment/tools/__init__.py` (empty)
- Create: `modules/enrichment/tools/shopify_order_history.py`
- Test: `tests/unit/test_shopify_order_history.py`

**Interfaces:**
- Consumes: `modules.enrichment.schemas.ShopifyCreds`.
- Produces: `async def fetch_order_history(creds: ShopifyCreds, *, email: str | None = None, phone: str | None = None) -> dict[str, Any]`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_shopify_order_history.py`:

```python
"""Unit tests for the deterministic Shopify order-history fetch."""

import inspect

import pytest

from modules.enrichment.schemas import ShopifyCreds
from modules.enrichment.tools import shopify_order_history as soh


async def test_fetch_raises_not_implemented_until_integration_lands() -> None:
    creds = ShopifyCreds(shop_domain="acme.myshopify.com", access_token="tok")
    with pytest.raises(NotImplementedError):
        await soh.fetch_order_history(creds, email="a@b.com", phone=None)


def test_output_contract_is_pii_free_by_design() -> None:
    # Guardrail: the source must not reference customer PII fields in its output.
    src = inspect.getsource(soh)
    for banned in ("customer_email", "customer_phone", "customer_name", "address"):
        assert banned not in src
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_shopify_order_history.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'modules.enrichment.tools'`.

- [ ] **Step 3: Implement the fetcher (stub)**

Create `modules/enrichment/tools/__init__.py` (empty) and `modules/enrichment/tools/shopify_order_history.py`:

```python
"""Deterministic Shopify order-history fetch — NOT an LLM tool.

Called by the enrichment service when a tenant has Shopify credentials. The
credentials never reach the LLM. The RETURN VALUE is PII-free by design: order
facts only (id, timestamps, amounts, currency, line-item title/qty/price) — never
customer email, phone, name, or address — so no PII enters the enrichment result,
the scoring context, or any trace.

The real Shopify Admin API call is deferred; the shape below documents the intended
output for the scoring agent.
"""

from typing import Any

from modules.enrichment.schemas import ShopifyCreds


async def fetch_order_history(
    creds: ShopifyCreds,
    *,
    email: str | None = None,
    phone: str | None = None,
) -> dict[str, Any]:
    """Return PII-free order facts for a customer identified by email/phone.

    Intended output shape (order facts only, no customer PII):
        {
            "customer_found": bool,
            "order_count": int,
            "total_spent": float,
            "currency": str,
            "orders": [
                {"id": str, "created_at": str, "total": float,
                 "line_items": [{"title": str, "qty": int, "price": float}]},
            ],
        }

    The real Shopify Admin API integration is pending.
    """
    raise NotImplementedError("Shopify order-history integration pending")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_shopify_order_history.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add modules/enrichment/tools/ tests/unit/test_shopify_order_history.py
git commit -m "feat: add PII-free Shopify order-history fetch interface (stub)"
```

---

## Task 9: Enrichment service (`modules/enrichment/service.py`)

Orchestrate: build a business-type goal → run the research agent → attach order history when Shopify creds are present → compose `EnrichmentResult`.

**Files:**
- Create: `modules/enrichment/service.py`
- Test: `tests/unit/test_enrichment_service.py`

**Interfaces:**
- Consumes: `shared.research.agent.research`, `modules.enrichment.tools.shopify_order_history.fetch_order_history`, schemas from Task 7, `shared.tenant.schemas.TenantRead`/`BusinessType`.
- Produces: `async def run_enrichment(lead: LeadPayload, tenant: TenantRead, deps: EnrichmentDeps | None = None) -> EnrichmentResult`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_enrichment_service.py`:

```python
"""Unit tests for modules/enrichment/service — mocks research + shopify."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.enrichment import service
from modules.enrichment.schemas import EnrichmentDeps, ResearchFindings, ShopifyCreds
from shared.events.schemas import LeadPayload, LeadSource
from shared.tenant.schemas import BusinessType


def _tenant(business_type: BusinessType) -> MagicMock:
    t = MagicMock()
    t.business_type = business_type
    t.company_name = "Acme"
    return t


def _lead() -> LeadPayload:
    return LeadPayload(name="Priya", email="p@acme.com", phone="+91", source=LeadSource.EMAIL)


async def test_run_enrichment_without_shopify_has_no_order_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    findings = ResearchFindings(company_info={"industry": "SaaS"}, confidence=0.8)
    monkeypatch.setattr(service, "research", AsyncMock(return_value=findings))
    fetch = AsyncMock()
    monkeypatch.setattr(service, "fetch_order_history", fetch)

    result = await service.run_enrichment(_lead(), _tenant(BusinessType.B2B))

    assert result.company_info == {"industry": "SaaS"}
    assert result.order_history is None
    fetch.assert_not_called()


async def test_run_enrichment_with_shopify_attaches_order_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    findings = ResearchFindings(confidence=0.6)
    monkeypatch.setattr(service, "research", AsyncMock(return_value=findings))
    orders = {"customer_found": True, "order_count": 3}
    monkeypatch.setattr(service, "fetch_order_history", AsyncMock(return_value=orders))

    deps = EnrichmentDeps(shopify_creds=ShopifyCreds(shop_domain="s.myshopify.com", access_token="t"))
    result = await service.run_enrichment(_lead(), _tenant(BusinessType.B2C), deps)

    assert result.order_history == orders


async def test_goal_varies_by_business_type(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    async def fake_research(*, goal: str, output_schema: type) -> ResearchFindings:
        captured["goal"] = goal
        return ResearchFindings(confidence=0.5)

    monkeypatch.setattr(service, "research", fake_research)
    await service.run_enrichment(_lead(), _tenant(BusinessType.B2B))
    b2b_goal = captured["goal"]
    await service.run_enrichment(_lead(), _tenant(BusinessType.B2C))
    b2c_goal = captured["goal"]
    assert b2b_goal != b2c_goal
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_enrichment_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'modules.enrichment.service'`.

- [ ] **Step 3: Implement the service**

Create `modules/enrichment/service.py`:

```python
"""Enrichment service: run the research agent and attach Shopify order history.

Both business types run the research agent; only the goal differs. Order history
is fetched deterministically (never by the LLM) and only when Shopify creds are
present. The creds-populating wiring and the triggering event consumer are deferred.
"""

from modules.enrichment.schemas import (
    EnrichmentDeps,
    EnrichmentResult,
    ResearchFindings,
)
from modules.enrichment.tools.shopify_order_history import fetch_order_history
from shared.events.schemas import LeadPayload
from shared.research.agent import research
from shared.tenant.schemas import BusinessType, TenantRead


def _build_goal(lead: LeadPayload, business_type: BusinessType) -> str:
    """Compose the research goal, shaped by business type."""
    who = lead.company or lead.name or lead.email or lead.phone or "this lead"
    if business_type is BusinessType.B2B:
        return (
            f"Research the company associated with {who}. Find its industry, products "
            f"or services, size, geography, and any recent notable news. Assess how "
            f"strong a B2B prospect it is."
        )
    return (
        f"Research {who} for consumer context: the brand, product interests, and any "
        f"public signals relevant to a B2C purchase. Keep it light and factual."
    )


async def run_enrichment(
    lead: LeadPayload,
    tenant: TenantRead,
    deps: EnrichmentDeps | None = None,
) -> EnrichmentResult:
    """Enrich a lead: web research + (if Shopify-connected) order history."""
    goal = _build_goal(lead, tenant.business_type)
    findings: ResearchFindings = await research(goal=goal, output_schema=ResearchFindings)

    order_history = None
    if deps is not None and deps.shopify_creds is not None:
        order_history = await fetch_order_history(
            deps.shopify_creds, email=lead.email, phone=lead.phone
        )

    return EnrichmentResult(**findings.model_dump(), order_history=order_history)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_enrichment_service.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add modules/enrichment/service.py tests/unit/test_enrichment_service.py
git commit -m "feat: add enrichment service (research + shopify order history)"
```

---

## Task 10: Reshape the persona agent (`modules/tenant_onboarding/agents/persona.py`)

Persona consumes researched `company_info` (a dict) instead of raw `website_text`.

**Files:**
- Modify: `modules/tenant_onboarding/agents/persona.py`
- Test: `tests/unit/test_onboarding_agents.py`

**Interfaces:**
- Produces: `async def run(company_name: str, business_type: BusinessType, company_info: dict[str, Any]) -> dict[str, Any]`.

- [ ] **Step 1: Update the persona test**

In `tests/unit/test_onboarding_agents.py`, update the persona test to call the new signature (replace the `website_text=...` call):

```python
async def test_persona_run_builds_profile_from_company_info() -> None:
    from modules.tenant_onboarding.agents import persona
    from shared.tenant.schemas import BusinessType

    profile = {"industry": "SaaS", "target_market": "SMB", "products_services": "CRM",
               "company_size": "50", "geography": "India", "value_proposition": "fast"}
    with patch(
        "modules.tenant_onboarding.agents.persona.call_with_tool",
        AsyncMock(return_value=profile),
    ) as mock_call:
        result = await persona.run(
            company_name="Acme",
            business_type=BusinessType.B2B,
            company_info={"summary": "Acme makes CRM", "industry": "SaaS"},
        )
    assert result == profile
    prompt = mock_call.call_args.kwargs["prompt"]
    assert "Acme makes CRM" in prompt
```

(Ensure the file imports `AsyncMock` and `patch` from `unittest.mock`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_onboarding_agents.py -k persona -v`
Expected: FAIL (`run()` got an unexpected keyword argument `company_info`, or a signature mismatch).

- [ ] **Step 3: Reshape `persona.run`**

In `modules/tenant_onboarding/agents/persona.py`, add `import json` and replace `run`:

```python
async def run(
    company_name: str,
    business_type: BusinessType,
    company_info: dict[str, Any],
) -> dict[str, Any]:
    """Return a business_profile dict derived from researched company info."""
    prompt = (
        f"You are analyzing a business to build its profile.\n\n"
        f"Company name: {company_name}\n"
        f"Business type: {business_type.value}\n\n"
        f"Researched company information (JSON):\n{json.dumps(company_info, indent=2)}\n\n"
        f"Extract a structured business profile based only on the information above. "
        f"Be factual and concise."
    )
    result: dict[str, Any] = await call_with_tool(
        prompt=prompt,
        tool_name=_TOOL_NAME,
        tool_description=_TOOL_DESCRIPTION,
        input_schema=_INPUT_SCHEMA,
    )
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_onboarding_agents.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add modules/tenant_onboarding/agents/persona.py tests/unit/test_onboarding_agents.py
git commit -m "refactor: persona consumes researched company_info instead of website text"
```

---

## Task 11: Cut onboarding over to the research agent (`modules/tenant_onboarding/pipeline.py`)

Replace the httpx website fetch with `research()`; define the `CompanyInfo` schema; drop the HTML helpers.

**Files:**
- Modify: `modules/tenant_onboarding/pipeline.py`
- Test: `tests/unit/test_onboarding_pipeline.py` (replace)

**Interfaces:**
- Consumes: `shared.research.agent.research`.
- Produces: `class CompanyInfo(BaseModel)` (summary, industry, products_services, target_market, notable_facts); unchanged `run_pipeline` signature.

- [ ] **Step 1: Replace the pipeline test file**

Replace the entire contents of `tests/unit/test_onboarding_pipeline.py`:

```python
"""Unit tests for modules/tenant_onboarding/pipeline — all I/O is mocked."""

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from modules.tenant_onboarding.pipeline import CompanyInfo, run_pipeline
from shared.tenant.schemas import BusinessType, OnboardingStatus


def _mock_tenant(tenant_id: UUID | None = None) -> MagicMock:
    t = MagicMock()
    t.id = tenant_id or uuid4()
    t.company_name = "Acme"
    t.business_type = BusinessType.B2B
    t.website_url = "https://acme.com"
    return t


def _patch_common(monkeypatch: pytest.MonkeyPatch, tenant_id: UUID) -> AsyncMock:
    set_status_mock = AsyncMock()
    monkeypatch.setattr(
        "modules.tenant_onboarding.pipeline.get_tenant",
        AsyncMock(return_value=_mock_tenant(tenant_id)),
    )
    monkeypatch.setattr("modules.tenant_onboarding.pipeline.set_onboarding_status", set_status_mock)
    return set_status_mock


async def test_pipeline_happy_path_sets_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant_id = uuid4()
    mock_session = AsyncMock()
    set_status_mock = _patch_common(monkeypatch, tenant_id)

    from shared.tenant_config.schemas import Dimension, Signal, Thresholds, Weights

    company_info = CompanyInfo(summary="Acme makes CRM", industry="SaaS")
    business_profile = {"industry": "SaaS", "target_market": "SMB"}
    icp_data = {"buyer_role": "VP Sales"}
    sigs = [Signal(id=f"{d.value.lower()}_1", dimension=d, question="?") for d in Dimension]
    weights = Weights(fit=0.2, intent=0.2, engagement=0.2, behaviour=0.2, context=0.2)
    thresholds = Thresholds(hot=80, warm=55)

    persona_run = AsyncMock(return_value=business_profile)
    monkeypatch.setattr(
        "modules.tenant_onboarding.pipeline.research",
        AsyncMock(return_value=company_info),
    )
    monkeypatch.setattr("modules.tenant_onboarding.pipeline.persona.run", persona_run)
    monkeypatch.setattr(
        "modules.tenant_onboarding.pipeline.icp.run", AsyncMock(return_value=icp_data)
    )
    monkeypatch.setattr(
        "modules.tenant_onboarding.pipeline.signals.run",
        AsyncMock(return_value=(sigs, weights, thresholds)),
    )
    monkeypatch.setattr("modules.tenant_onboarding.pipeline.create_active", AsyncMock())
    monkeypatch.setattr("modules.tenant_onboarding.pipeline.activate_tenant", AsyncMock())

    await run_pipeline(mock_session, tenant_id)

    calls = [c.args[2] for c in set_status_mock.call_args_list]
    assert OnboardingStatus.RUNNING in calls
    assert OnboardingStatus.COMPLETE in calls
    # persona received the researched company_info as a dict
    assert persona_run.call_args.kwargs["company_info"] == company_info.model_dump()


async def test_pipeline_sets_failed_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant_id = uuid4()
    mock_session = AsyncMock()
    set_status_mock = _patch_common(monkeypatch, tenant_id)

    monkeypatch.setattr(
        "modules.tenant_onboarding.pipeline.research",
        AsyncMock(side_effect=Exception("mcp down")),
    )

    with pytest.raises(Exception, match="mcp down"):
        await run_pipeline(mock_session, tenant_id)

    calls = [c.args[2] for c in set_status_mock.call_args_list]
    assert OnboardingStatus.RUNNING in calls
    assert OnboardingStatus.FAILED in calls
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_onboarding_pipeline.py -v`
Expected: FAIL with `ImportError: cannot import name 'CompanyInfo'`.

- [ ] **Step 3: Rewrite the pipeline**

Replace `modules/tenant_onboarding/pipeline.py` with:

```python
"""Pipeline: research the tenant's company, then run the three agents in sequence."""

from typing import Any
from uuid import UUID

from langfuse.decorators import langfuse_context, observe
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from modules.tenant_onboarding.agents import icp, persona, signals
from shared.research.agent import research
from shared.tenant.schemas import OnboardingStatus
from shared.tenant.service import activate_tenant, get_tenant, set_onboarding_status
from shared.tenant_config.schemas import TenantConfigCreate
from shared.tenant_config.service import create_active


class CompanyInfo(BaseModel):
    """Structured company facts gathered by the research agent for onboarding."""

    summary: str = ""
    industry: str = ""
    products_services: str = ""
    target_market: str = ""
    notable_facts: list[str] = Field(default_factory=list)


def _company_goal(website_url: str, company_name: str) -> str:
    """Compose the research goal for a tenant's own company."""
    return (
        f"Research the company '{company_name}' (website: {website_url}). Summarize what "
        f"it does, its industry, its products or services, its target market, and any "
        f"notable facts. Base everything only on what you find."
    )


@observe(capture_input=False)
async def run_pipeline(session: AsyncSession, tenant_id: UUID) -> None:
    """Run the full onboarding pipeline: research → 3 agents → activate."""
    langfuse_context.update_current_trace(
        name="tenant-onboarding",
        metadata={"tenant_id": str(tenant_id)},
        tags=[str(tenant_id)],
    )
    await set_onboarding_status(session, tenant_id, OnboardingStatus.RUNNING)
    try:
        tenant = await get_tenant(session, tenant_id)

        company_info = await research(
            goal=_company_goal(str(tenant.website_url), tenant.company_name),
            output_schema=CompanyInfo,
        )

        business_profile: dict[str, Any] = await persona.run(
            company_name=tenant.company_name,
            business_type=tenant.business_type,
            company_info=company_info.model_dump(),
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_onboarding_pipeline.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add modules/tenant_onboarding/pipeline.py tests/unit/test_onboarding_pipeline.py
git commit -m "refactor: onboarding uses the shared research agent (drop httpx fetch)"
```

---

## Task 12: Deploy the MCP server + final gate (`docker-compose.yml`)

Add the `mcp-web-search` service and point app + worker at it.

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Add the service and wire the URL**

In `docker-compose.yml`, add a new service (mirroring `app`'s build/env style):

```yaml
  mcp-web-search:
    build: .
    restart: unless-stopped
    env_file: .env
    environment:
      PYTHONPATH: /app
      FASTMCP_HOST: 0.0.0.0
      FASTMCP_PORT: "8000"
    command: /app/.venv/bin/python -m mcp_servers.web_search.server
    mem_limit: 256m
    cpus: "0.5"
```

Add to **both** the `app` and `worker` `environment:` blocks:

```yaml
      MCP_WEB_SEARCH_URL: http://mcp-web-search:8000/mcp
```

And add to the `app` and `worker` `depends_on:` blocks:

```yaml
      mcp-web-search:
        condition: service_started
```

- [ ] **Step 2: Validate the compose file**

Run: `docker compose config >/dev/null && echo OK`
Expected: `OK` (no YAML/interpolation errors).

- [ ] **Step 3: Run the full local gate**

Run: `make ci`
Expected: lint + typecheck + full test suite all pass.

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml
git commit -m "chore: run Serper web_search MCP server as a compose service"
```

---

## Self-Review Notes

- **Spec coverage:** MCP server → Task 4; research agent → Task 5; `get_chat_model` → Task 3; deps/config → Task 2; Serper `search` → Task 1; fattened events + Option A wiring → Task 6; enrichment schemas → Task 7; PII-free Shopify fetch → Task 8; enrichment service (hybrid goal, order-history attach) → Task 9; persona reshape → Task 10; onboarding cutover → Task 11; deploy + `make ci` → Task 12. Deferred items (real Shopify API, creds-populating wiring, triggering consumer, persistence, MCP pooling) are intentionally not tasked.
- **Type consistency:** `research(*, goal, output_schema) -> T` is called identically in Task 9 (`output_schema=ResearchFindings`) and Task 11 (`output_schema=CompanyInfo`). `EnrichmentResult(**findings.model_dump(), order_history=...)` matches `ResearchFindings` fields (Task 7) + the `order_history` field (Task 6). `fetch_order_history(creds, *, email, phone)` signature matches its call in Task 9. `get_chat_model()` (Task 3) is consumed in Task 5.
- **Note on `make ci`:** deferred to Task 12 — the first point at which the whole repo is in its final shape (compose + all wiring present).
