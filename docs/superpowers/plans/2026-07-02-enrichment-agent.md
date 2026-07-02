# Enrichment Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reusable self-querying web-research agent plus a Shopify order-history tool, wire them into lead enrichment, and cut the tenant-onboarding pipeline over to the same agent.

**Architecture:** A tool-agnostic multi-turn tool-calling loop lands in `clients/groq_client`. A reusable ReAct research agent (`research()` + `web_search` tool + a `Tool` protocol) lands in `shared/research`, imported by both `modules/enrichment` and `modules/tenant_onboarding`. Enrichment adds its own `shopify_order_history` tool and assembles a per-tenant toolset. Cross-module data uses fattened `shared/events` contracts (event-carried state).

**Tech Stack:** Python 3.13, async, Groq (`llama-3.3-70b-versatile`, temperature 0, function calling), Serper, httpx, pydantic v2, Langfuse (`@observe`), pytest (`asyncio_mode=auto`), mypy strict, ruff (line length 100).

## Global Constraints

- Dependency rule: `modules → shared → clients → core`. No module imports another module. `shared/` imports nothing from `modules/`.
- TDD: write the failing test, watch it fail for the right reason, minimal code, then refactor. Commit after each green task.
- Unit tests must not need a DB or network — mock `AsyncGroq` via `patch("clients.groq_client.AsyncGroq")`, mock httpx via `patch("<module>.httpx.AsyncClient")`, mock Langfuse via `patch("<module>.langfuse_context")`.
- `mypy .` runs strict over the whole repo including `tests/`. `ruff` line length 100 (E501 ignored).
- Groq model constant is `llama-3.3-70b-versatile` (`clients.groq_client._MODEL`); temperature 0 everywhere.
- External failures raise `core.exceptions.ExternalServiceError`.
- Run the local gate before finishing: `make ci` (lint + typecheck + test).

---

## Phase 1 — Clients foundation

### Task 1: Serper query search (`clients/serper_client.py`)

Add a query-based search that returns an LLM-readable text digest (knowledge graph + organic snippets). The existing `search_site_pages` stays untouched.

**Files:**
- Modify: `clients/serper_client.py`
- Test: `tests/unit/test_serper_client.py`

**Interfaces:**
- Produces: `async def search(query: str, num: int = 5) -> str` — raises `ExternalServiceError` on HTTP/network failure.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_serper_client.py`:

```python
from clients.serper_client import search


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

- [ ] **Step 3: Implement `search`**

Append to `clients/serper_client.py`:

```python
async def search(query: str, num: int = 5) -> str:
    """Run a web search and return a compact text digest for an LLM to read.

    Combines the knowledge graph (if any) with the top organic snippets. Returns
    an empty string when Serper has nothing. Raises ExternalServiceError on
    HTTP errors or network failures.
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
Expected: PASS (all, old and new).

- [ ] **Step 5: Commit**

```bash
git add clients/serper_client.py tests/unit/test_serper_client.py
git commit -m "feat: add query-based Serper search returning an LLM digest"
```

---

### Task 2: Multi-turn tool loop (`clients/groq_client.py`)

Add a multi-turn tool-calling loop beside the untouched single-shot `call_with_tool`. Each Groq turn is traced as its own Langfuse generation.

**Files:**
- Modify: `clients/groq_client.py`
- Test: `tests/unit/test_groq_client.py`

**Interfaces:**
- Consumes: `AsyncGroq`, `get_settings`, `ExternalServiceError`, `langfuse_context`, `observe` (already imported).
- Produces:
  - `async def run_tool_loop(*, messages: list[dict[str, Any]], tools: list[dict[str, Any]], dispatch: Callable[[str, dict[str, Any]], Awaitable[str]], max_iterations: int = 6, model: str = _MODEL, max_tokens: int = 2048) -> list[dict[str, Any]]` — returns the full conversation (assistant + tool messages appended). Raises `ExternalServiceError` on API failure.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_groq_client.py`:

```python
from clients.groq_client import run_tool_loop


def _tool_call(call_id: str, name: str, args: dict[str, Any]) -> MagicMock:
    call = MagicMock()
    call.id = call_id
    call.function.name = name
    call.function.arguments = json.dumps(args)
    return call


def _assistant_response(content: str, tool_calls: list[MagicMock] | None) -> MagicMock:
    message = MagicMock()
    message.content = content
    message.tool_calls = tool_calls
    choice = MagicMock()
    choice.message = message
    response = MagicMock()
    response.choices = [choice]
    response.usage.prompt_tokens = 10
    response.usage.completion_tokens = 5
    return response


async def test_run_tool_loop_executes_tool_then_stops() -> None:
    first = _assistant_response("", [_tool_call("c1", "web_search", {"query": "acme"})])
    second = _assistant_response("done", None)
    mock_create = AsyncMock(side_effect=[first, second])
    dispatch = AsyncMock(return_value="search result text")

    with patch("clients.groq_client.AsyncGroq") as mock_client_cls:
        mock_client_cls.return_value.chat.completions.create = mock_create
        convo = await run_tool_loop(
            messages=[{"role": "user", "content": "research acme"}],
            tools=[{"type": "function", "function": {"name": "web_search"}}],
            dispatch=dispatch,
        )

    dispatch.assert_awaited_once_with("web_search", {"query": "acme"})
    roles = [m["role"] for m in convo]
    assert "tool" in roles
    tool_msg = next(m for m in convo if m["role"] == "tool")
    assert tool_msg["content"] == "search result text"
    assert tool_msg["tool_call_id"] == "c1"
    assert mock_create.await_count == 2


async def test_run_tool_loop_respects_iteration_cap() -> None:
    looping = _assistant_response("", [_tool_call("c", "web_search", {"query": "x"})])
    mock_create = AsyncMock(return_value=looping)
    dispatch = AsyncMock(return_value="more")

    with patch("clients.groq_client.AsyncGroq") as mock_client_cls:
        mock_client_cls.return_value.chat.completions.create = mock_create
        await run_tool_loop(
            messages=[{"role": "user", "content": "loop"}],
            tools=[{"type": "function", "function": {"name": "web_search"}}],
            dispatch=dispatch,
            max_iterations=3,
        )

    assert mock_create.await_count == 3


async def test_run_tool_loop_raises_on_api_error() -> None:
    with patch("clients.groq_client.AsyncGroq") as mock_client_cls:
        mock_client_cls.return_value.chat.completions.create = AsyncMock(
            side_effect=Exception("network error")
        )
        with pytest.raises(ExternalServiceError, match="Groq API call failed"):
            await run_tool_loop(
                messages=[{"role": "user", "content": "x"}],
                tools=[],
                dispatch=AsyncMock(return_value=""),
            )


async def test_run_tool_loop_traces_each_turn() -> None:
    first = _assistant_response("", [_tool_call("c1", "web_search", {"query": "acme"})])
    second = _assistant_response("done", None)
    with (
        patch("clients.groq_client.AsyncGroq") as mock_client_cls,
        patch("clients.groq_client.langfuse_context") as mock_ctx,
    ):
        mock_client_cls.return_value.chat.completions.create = AsyncMock(
            side_effect=[first, second]
        )
        await run_tool_loop(
            messages=[{"role": "user", "content": "research acme"}],
            tools=[{"type": "function", "function": {"name": "web_search"}}],
            dispatch=AsyncMock(return_value="r"),
        )

    assert mock_ctx.update_current_observation.call_count == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_groq_client.py -k run_tool_loop -v`
Expected: FAIL with `ImportError: cannot import name 'run_tool_loop'`.

- [ ] **Step 3: Implement `run_tool_loop`**

Add to the top of `clients/groq_client.py` (imports):

```python
from collections.abc import Awaitable, Callable
```

Append to `clients/groq_client.py`:

```python
@observe()
async def run_tool_loop(
    *,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    dispatch: Callable[[str, dict[str, Any]], Awaitable[str]],
    max_iterations: int = 6,
    model: str = _MODEL,
    max_tokens: int = 2048,
) -> list[dict[str, Any]]:
    """Drive a multi-turn tool-calling loop.

    Calls Groq with `tools` and tool_choice="auto"; each time the model returns
    tool calls, runs each via `dispatch(name, args)` and feeds the result back as
    a tool message. Stops when the model returns no tool calls or `max_iterations`
    is reached. Returns the full message list. Raises ExternalServiceError on any
    API failure.
    """
    client = AsyncGroq(api_key=get_settings().groq_api_key)
    convo: list[dict[str, Any]] = list(messages)
    for _ in range(max_iterations):
        response = await _completion(client, model, convo, tools, max_tokens)
        message = response.choices[0].message
        convo.append(_assistant_message(message))
        tool_calls = message.tool_calls
        if not tool_calls:
            break
        for call in tool_calls:
            args: dict[str, Any] = json.loads(call.function.arguments)
            result = await dispatch(call.function.name, args)
            convo.append(
                {"role": "tool", "tool_call_id": call.id, "content": result}
            )
    return convo


@observe(as_type="generation")
async def _completion(
    client: AsyncGroq,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    max_tokens: int,
) -> Any:
    """One traced Groq completion in a tool loop turn."""
    try:
        response = await client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            temperature=0.0,
            tools=tools,
            tool_choice="auto",
            messages=messages,
        )
    except Exception as exc:
        raise ExternalServiceError(f"Groq API call failed: {exc}") from exc

    usage = (
        {"input": response.usage.prompt_tokens, "output": response.usage.completion_tokens}
        if response.usage is not None
        else None
    )
    langfuse_context.update_current_observation(
        name="tool-loop-turn", model=model, input=messages, usage=usage
    )
    return response


def _assistant_message(message: Any) -> dict[str, Any]:
    """Serialize the model's assistant message (with any tool calls) back to a dict."""
    out: dict[str, Any] = {"role": "assistant", "content": message.content or ""}
    tool_calls = message.tool_calls or []
    if tool_calls:
        out["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.function.name,
                    "arguments": call.function.arguments,
                },
            }
            for call in tool_calls
        ]
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_groq_client.py -v`
Expected: PASS (old and new).

- [ ] **Step 5: Commit**

```bash
git add clients/groq_client.py tests/unit/test_groq_client.py
git commit -m "feat: add multi-turn run_tool_loop to groq_client, traced per turn"
```

---

## Phase 2 — Shared research agent

### Task 3: Tool protocol (`shared/research/tools/base.py`)

**Files:**
- Create: `shared/research/__init__.py`
- Create: `shared/research/tools/__init__.py`
- Create: `shared/research/tools/base.py`
- Test: `tests/unit/test_research_tools.py`

**Interfaces:**
- Produces: `class Tool(Protocol)` with attributes `name: str`, `description: str`, `parameters: dict[str, Any]`, and `async def run(self, **kwargs: Any) -> str`.
- Produces: `def to_tool_def(tool: Tool) -> dict[str, Any]` — Groq/OpenAI function-tool definition.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_research_tools.py`:

```python
"""Unit tests for shared.research tools."""

from typing import Any

from shared.research.tools.base import Tool, to_tool_def


class _FakeTool:
    name: str = "fake"
    description: str = "A fake tool."
    parameters: dict[str, Any] = {"type": "object", "properties": {}, "required": []}

    async def run(self, **kwargs: Any) -> str:
        return "ran"


def test_to_tool_def_shape() -> None:
    tool: Tool = _FakeTool()
    definition = to_tool_def(tool)
    assert definition == {
        "type": "function",
        "function": {
            "name": "fake",
            "description": "A fake tool.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_research_tools.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shared.research'`.

- [ ] **Step 3: Create the package and protocol**

Create `shared/research/__init__.py` (empty). Create `shared/research/tools/__init__.py` (empty). Create `shared/research/tools/base.py`:

```python
"""The Tool protocol used by the research agent, plus its Groq serialization."""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Tool(Protocol):
    """A callable capability the research agent can invoke.

    `parameters` is a JSON-Schema object describing `run`'s keyword arguments.
    `run` returns a text result that is fed back into the model conversation.
    """

    name: str
    description: str
    parameters: dict[str, Any]

    async def run(self, **kwargs: Any) -> str: ...


def to_tool_def(tool: Tool) -> dict[str, Any]:
    """Serialize a Tool into a Groq/OpenAI function-tool definition."""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        },
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_research_tools.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add shared/research/__init__.py shared/research/tools/__init__.py shared/research/tools/base.py tests/unit/test_research_tools.py
git commit -m "feat: add Tool protocol for the research agent"
```

---

### Task 4: web_search tool (`shared/research/tools/web_search.py`)

**Files:**
- Create: `shared/research/tools/web_search.py`
- Test: `tests/unit/test_research_tools.py`

**Interfaces:**
- Consumes: `clients.serper_client.search`, `shared.research.tools.base.Tool`.
- Produces: `class WebSearchTool` (name `"web_search"`, `parameters` requiring `query`) and a module-level instance `web_search: WebSearchTool`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_research_tools.py`:

```python
from unittest.mock import AsyncMock, patch


async def test_web_search_tool_calls_serper_with_query() -> None:
    from shared.research.tools.web_search import web_search

    with patch(
        "shared.research.tools.web_search.search",
        new=AsyncMock(return_value="digest text"),
    ) as mock_search:
        result = await web_search.run(query="acme inc funding")

    assert result == "digest text"
    mock_search.assert_awaited_once_with("acme inc funding")


def test_web_search_tool_metadata() -> None:
    from shared.research.tools.web_search import web_search

    assert web_search.name == "web_search"
    assert web_search.parameters["required"] == ["query"]
    assert "query" in web_search.parameters["properties"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_research_tools.py -k web_search -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shared.research.tools.web_search'`.

- [ ] **Step 3: Implement the tool**

Create `shared/research/tools/web_search.py`:

```python
"""The web_search tool — a dumb pipe to Serper. The agent composes the query."""

from typing import Any

from clients.serper_client import search


class WebSearchTool:
    """Search the web for a query the agent composes; returns a text digest."""

    name: str = "web_search"
    description: str = (
        "Search the web for information about a company or person. Provide a "
        "focused query string; returns a text digest of the top results and "
        "knowledge graph. Call again with a refined query if you need more."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The search query to run."}
        },
        "required": ["query"],
    }

    async def run(self, **kwargs: Any) -> str:
        query = str(kwargs.get("query", "")).strip()
        return await search(query)


web_search = WebSearchTool()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_research_tools.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add shared/research/tools/web_search.py tests/unit/test_research_tools.py
git commit -m "feat: add web_search tool over Serper"
```

---

### Task 5: research agent loop (`shared/research/agent.py`)

**Files:**
- Create: `shared/research/agent.py`
- Test: `tests/unit/test_research_agent.py`

**Interfaces:**
- Consumes: `clients.groq_client.run_tool_loop`, `clients.groq_client.call_with_tool`, `shared.research.tools.base.Tool`/`to_tool_def`, `core.exceptions.ExternalServiceError`.
- Produces: `async def research(*, goal: str, tools: Sequence[Tool], output_schema: dict[str, Any], output_tool_name: str = "record_findings", output_tool_description: str = "Record the structured findings gathered for the goal.", max_iterations: int = 6) -> dict[str, Any]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_research_agent.py`:

```python
"""Unit tests for shared.research.agent — Groq layer is mocked."""

from typing import Any
from unittest.mock import AsyncMock, patch

from core.exceptions import ExternalServiceError
from shared.research.agent import research


class _RecordingTool:
    name: str = "fake_tool"
    description: str = "records calls"
    parameters: dict[str, Any] = {"type": "object", "properties": {}, "required": []}

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def run(self, **kwargs: Any) -> str:
        self.calls.append(kwargs)
        return "tool output"


class _FailingTool:
    name: str = "boom_tool"
    description: str = "always fails"
    parameters: dict[str, Any] = {"type": "object", "properties": {}, "required": []}

    async def run(self, **kwargs: Any) -> str:
        raise ExternalServiceError("serper down")


async def test_research_returns_structured_output() -> None:
    with (
        patch(
            "shared.research.agent.run_tool_loop",
            new=AsyncMock(return_value=[{"role": "assistant", "content": "notes"}]),
        ),
        patch(
            "shared.research.agent.call_with_tool",
            new=AsyncMock(return_value={"confidence": 0.9}),
        ) as mock_final,
    ):
        result = await research(
            goal="enrich acme",
            tools=[_RecordingTool()],
            output_schema={"type": "object", "properties": {}, "required": []},
        )

    assert result == {"confidence": 0.9}
    mock_final.assert_awaited_once()


async def test_research_dispatch_runs_the_named_tool() -> None:
    tool = _RecordingTool()

    async def fake_loop(*, messages: Any, tools: Any, dispatch: Any, **kwargs: Any) -> list[Any]:
        # Simulate the model calling the tool once.
        out = await dispatch("fake_tool", {"query": "acme"})
        assert out == "tool output"
        return list(messages)

    with (
        patch("shared.research.agent.run_tool_loop", new=fake_loop),
        patch("shared.research.agent.call_with_tool", new=AsyncMock(return_value={})),
    ):
        await research(
            goal="g",
            tools=[tool],
            output_schema={"type": "object", "properties": {}, "required": []},
        )

    assert tool.calls == [{"query": "acme"}]


async def test_research_dispatch_swallows_tool_errors() -> None:
    captured: dict[str, str] = {}

    async def fake_loop(*, messages: Any, tools: Any, dispatch: Any, **kwargs: Any) -> list[Any]:
        captured["result"] = await dispatch("boom_tool", {})
        return list(messages)

    with (
        patch("shared.research.agent.run_tool_loop", new=fake_loop),
        patch("shared.research.agent.call_with_tool", new=AsyncMock(return_value={})),
    ):
        await research(
            goal="g",
            tools=[_FailingTool()],
            output_schema={"type": "object", "properties": {}, "required": []},
        )

    assert "serper down" in captured["result"]


async def test_research_dispatch_reports_unknown_tool() -> None:
    captured: dict[str, str] = {}

    async def fake_loop(*, messages: Any, tools: Any, dispatch: Any, **kwargs: Any) -> list[Any]:
        captured["result"] = await dispatch("nonexistent", {})
        return list(messages)

    with (
        patch("shared.research.agent.run_tool_loop", new=fake_loop),
        patch("shared.research.agent.call_with_tool", new=AsyncMock(return_value={})),
    ):
        await research(
            goal="g",
            tools=[_RecordingTool()],
            output_schema={"type": "object", "properties": {}, "required": []},
        )

    assert "unknown tool" in captured["result"].lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_research_agent.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shared.research.agent'`.

- [ ] **Step 3: Implement the agent**

Create `shared/research/agent.py`:

```python
"""The reusable self-querying research agent (ReAct loop over injected tools)."""

from collections.abc import Sequence
from typing import Any

from clients.groq_client import call_with_tool, run_tool_loop
from core.exceptions import ExternalServiceError
from shared.research.tools.base import Tool, to_tool_def

_SYSTEM = (
    "You are a research agent. Given a goal, gather the information needed to "
    "satisfy it by composing your own web-search queries and reading the results. "
    "Search iteratively: refine your query when results are thin, and stop as soon "
    "as you have enough to answer confidently. Do not fabricate facts."
)


async def research(
    *,
    goal: str,
    tools: Sequence[Tool],
    output_schema: dict[str, Any],
    output_tool_name: str = "record_findings",
    output_tool_description: str = "Record the structured findings gathered for the goal.",
    max_iterations: int = 6,
) -> dict[str, Any]:
    """Run a self-querying research loop toward `goal`, then return a dict
    matching `output_schema`.

    The agent chooses which of `tools` to call and what queries to compose. Tool
    failures are surfaced back to the model as text so the loop can continue with
    other tools rather than crashing.
    """
    by_name: dict[str, Tool] = {tool.name: tool for tool in tools}
    tool_defs = [to_tool_def(tool) for tool in tools]

    async def dispatch(name: str, args: dict[str, Any]) -> str:
        tool = by_name.get(name)
        if tool is None:
            return f"Error: unknown tool '{name}'"
        try:
            return await tool.run(**args)
        except ExternalServiceError as exc:
            return f"Error: {exc}"

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": goal},
    ]
    convo = await run_tool_loop(
        messages=messages,
        tools=tool_defs,
        dispatch=dispatch,
        max_iterations=max_iterations,
    )

    transcript = _render_transcript(convo)
    return await call_with_tool(
        prompt=(
            f"{goal}\n\nResearch gathered:\n{transcript}\n\n"
            "Record the structured findings based only on the research above. "
            "Set a confidence between 0 and 1 reflecting how well-supported the "
            "findings are."
        ),
        tool_name=output_tool_name,
        tool_description=output_tool_description,
        input_schema=output_schema,
    )


def _render_transcript(convo: list[dict[str, Any]]) -> str:
    """Flatten the loop conversation into readable text for the final extraction."""
    lines: list[str] = []
    for message in convo:
        role = message.get("role", "")
        content = message.get("content", "")
        if content:
            lines.append(f"[{role}] {content}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_research_agent.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add shared/research/agent.py tests/unit/test_research_agent.py
git commit -m "feat: add self-querying research agent in shared/research"
```

---

## Phase 3 — Contracts and enrichment module

### Task 6: Fatten event contracts (`shared/events/schemas.py`)

Add `LeadPayload` and `EnrichmentResult`; fatten `LeadReceived` with a `payload` and `LeadEnriched` with a `result` (event-carried state).

**Files:**
- Modify: `shared/events/schemas.py`
- Test: `tests/unit/test_event_schemas.py`

**Interfaces:**
- Produces: `class LeadPayload(BaseModel)`, `class EnrichmentResult(BaseModel)`, `LeadReceived.payload: LeadPayload`, `LeadEnriched.result: EnrichmentResult`.

- [ ] **Step 1: Update the failing tests**

In `tests/unit/test_event_schemas.py`, update the import block to add the new models:

```python
from shared.events.schemas import (
    EnrichmentResult,
    Event,
    LeadBucket,
    LeadEnriched,
    LeadPayload,
    LeadReceived,
    LeadScored,
    LeadSource,
    TenantActivated,
)
```

Replace `test_lead_received_carries_lead_id_and_source` with:

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
```

Replace `test_lead_enriched_carries_lead_id` with:

```python
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

(Leave `test_lead_received_requires_lead_id_and_source`, `test_lead_received_rejects_invalid_source`, and `test_lead_enriched_requires_lead_id` as-is — they still assert on missing required fields.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_event_schemas.py -v`
Expected: FAIL with `ImportError: cannot import name 'LeadPayload'`.

- [ ] **Step 3: Implement the schema changes**

In `shared/events/schemas.py`, update the typing import line:

```python
from typing import Any, Literal
```

Add these two models above `class Event`:

```python
class LeadPayload(BaseModel):
    """The normalized lead data carried on LeadReceived (event-carried state).

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
    """The structured enrichment output carried on LeadEnriched."""

    model_config = ConfigDict(frozen=True)

    company_info: dict[str, Any] = Field(default_factory=dict)
    person_info: dict[str, Any] = Field(default_factory=dict)
    order_history: dict[str, Any] | None = None
    sources: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    reasoning_trace: str = ""
```

Replace the `LeadReceived` and `LeadEnriched` classes with:

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

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_event_schemas.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add shared/events/schemas.py tests/unit/test_event_schemas.py
git commit -m "feat: fatten LeadReceived/LeadEnriched with event-carried payload and result"
```

---

### Task 7: Enrichment schemas (`modules/enrichment/schemas.py`)

**Files:**
- Create: `modules/enrichment/schemas.py`
- Test: `tests/unit/test_enrichment_schemas.py`

**Interfaces:**
- Consumes: `shared.events.schemas.LeadPayload`, `shared.events.schemas.EnrichmentResult`.
- Produces: `EnrichmentInput` (alias of `LeadPayload`) and re-exported `EnrichmentResult`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_enrichment_schemas.py`:

```python
"""Unit tests for modules.enrichment.schemas."""

from shared.events.schemas import EnrichmentResult as EventEnrichmentResult
from shared.events.schemas import LeadPayload, LeadSource


def test_enrichment_input_is_lead_payload() -> None:
    from modules.enrichment.schemas import EnrichmentInput

    assert EnrichmentInput is LeadPayload
    obj = EnrichmentInput(email="a@b.com", source=LeadSource.EMAIL)
    assert obj.email == "a@b.com"


def test_enrichment_result_reexported() -> None:
    from modules.enrichment.schemas import EnrichmentResult

    assert EnrichmentResult is EventEnrichmentResult
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_enrichment_schemas.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'modules.enrichment.schemas'`.

- [ ] **Step 3: Implement the schemas module**

Create `modules/enrichment/schemas.py`:

```python
"""Public schemas for the enrichment module.

EnrichmentInput mirrors the LeadReceived payload; EnrichmentResult is the
LeadEnriched payload. Both are defined once in shared/events (the contract) and
re-exposed here as the module's public surface.
"""

from shared.events.schemas import EnrichmentResult, LeadPayload

EnrichmentInput = LeadPayload

__all__ = ["EnrichmentInput", "EnrichmentResult"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_enrichment_schemas.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add modules/enrichment/schemas.py tests/unit/test_enrichment_schemas.py
git commit -m "feat: add enrichment module schemas (input/result surface)"
```

---

### Task 8: Shopify order-history tool (`modules/enrichment/tools/shopify_order_history.py`)

Interface complete; real Shopify API call deferred.

**Files:**
- Create: `modules/enrichment/tools/__init__.py`
- Create: `modules/enrichment/tools/shopify_order_history.py`
- Test: `tests/unit/test_shopify_tool.py`

**Interfaces:**
- Produces: `class ShopifyCreds(BaseModel)` (`shop_domain: str`, `access_token: str`); `class ShopifyOrderHistoryTool` (name `"shopify_order_history"`, `__init__(self, creds: ShopifyCreds | None = None, max_orders: int = 50)`).

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_shopify_tool.py`:

```python
"""Unit tests for the Shopify order-history tool (integration deferred)."""

import json

import pytest

from modules.enrichment.tools.shopify_order_history import (
    ShopifyCreds,
    ShopifyOrderHistoryTool,
)


async def test_returns_not_configured_when_no_creds() -> None:
    tool = ShopifyOrderHistoryTool(creds=None)
    raw = await tool.run(email="a@b.com")
    payload = json.loads(raw)
    assert payload["customer_found"] is False
    assert payload["orders"] == []
    assert "not configured" in payload["note"].lower()


async def test_raises_not_implemented_when_creds_present() -> None:
    tool = ShopifyOrderHistoryTool(
        creds=ShopifyCreds(shop_domain="acme.myshopify.com", access_token="tok")
    )
    with pytest.raises(NotImplementedError, match="not yet implemented"):
        await tool.run(email="a@b.com")


def test_tool_metadata_allows_email_and_phone() -> None:
    tool = ShopifyOrderHistoryTool()
    assert tool.name == "shopify_order_history"
    assert set(tool.parameters["properties"]) == {"email", "phone"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_shopify_tool.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'modules.enrichment.tools'`.

- [ ] **Step 3: Implement the tool**

Create `modules/enrichment/tools/__init__.py` (empty). Create `modules/enrichment/tools/shopify_order_history.py`:

```python
"""Shopify order-history tool. Interface frozen now; real API call deferred.

Looks a customer up by email (primary) or phone (fallback) and returns their
raw recent orders for the agent to reason over. Until Shopify credentials and a
clients/shopify_client exist, this returns a not-configured payload (creds None)
or raises NotImplementedError (creds supplied).
"""

import json
from typing import Any

from pydantic import BaseModel


class ShopifyCreds(BaseModel):
    """Per-tenant Shopify Admin API credentials (populated once integration lands)."""

    shop_domain: str
    access_token: str


class ShopifyOrderHistoryTool:
    """Look up a customer's past Shopify orders by email or phone."""

    name: str = "shopify_order_history"
    description: str = (
        "Look up a customer's past Shopify orders by email (primary) or phone "
        "(fallback). Returns raw recent orders with line items, amounts, and dates."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "email": {"type": "string", "description": "Customer email to look up."},
            "phone": {"type": "string", "description": "Customer phone (fallback)."},
        },
        "required": [],
    }

    def __init__(self, creds: ShopifyCreds | None = None, max_orders: int = 50) -> None:
        self._creds = creds
        self._max_orders = max_orders

    async def run(self, **kwargs: Any) -> str:
        if self._creds is None:
            return json.dumps(
                {
                    "customer_found": False,
                    "orders": [],
                    "note": "Shopify integration not configured",
                }
            )
        # Deferred: call the Shopify Admin API via clients/shopify_client, look up
        # the customer by email/phone, and return up to self._max_orders raw orders.
        raise NotImplementedError("Shopify Admin API integration is not yet implemented")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_shopify_tool.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add modules/enrichment/tools/__init__.py modules/enrichment/tools/shopify_order_history.py tests/unit/test_shopify_tool.py
git commit -m "feat: add Shopify order-history tool interface (integration deferred)"
```

---

### Task 9: Enrichment service (`modules/enrichment/service.py`)

Assemble the per-tenant toolset (Option A) and run the agent.

**Files:**
- Create: `modules/enrichment/service.py`
- Test: `tests/unit/test_enrichment_service.py`

**Interfaces:**
- Consumes: `shared.research.agent.research`, `shared.research.tools.web_search.web_search`, `shared.research.tools.base.Tool`, `shared.tenant.schemas.BusinessType`, `modules.enrichment.schemas.EnrichmentInput`/`EnrichmentResult`, `modules.enrichment.tools.shopify_order_history.ShopifyOrderHistoryTool`/`ShopifyCreds`.
- Produces:
  - `class EnrichmentDeps` with `shopify_connected(self, tenant: Any) -> bool` (default False) and `shopify_creds(self, tenant: Any) -> ShopifyCreds | None` (default None).
  - `def build_tools(tenant: Any, deps: EnrichmentDeps) -> list[Tool]`.
  - `async def run_enrichment(lead: EnrichmentInput, tenant: Any, deps: EnrichmentDeps | None = None) -> EnrichmentResult`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_enrichment_service.py`:

```python
"""Unit tests for modules.enrichment.service — the agent layer is mocked."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from modules.enrichment.schemas import EnrichmentInput, EnrichmentResult
from modules.enrichment.service import EnrichmentDeps, build_tools, run_enrichment
from modules.enrichment.tools.shopify_order_history import ShopifyCreds
from shared.events.schemas import LeadSource
from shared.tenant.schemas import BusinessType


def _tenant(business_type: BusinessType) -> MagicMock:
    t = MagicMock()
    t.business_type = business_type
    t.company_name = "Acme"
    return t


def test_build_tools_b2b_gets_web_search_only() -> None:
    tools = build_tools(_tenant(BusinessType.B2B), EnrichmentDeps())
    assert [t.name for t in tools] == ["web_search"]


def test_build_tools_b2c_without_shopify_gets_web_search_only() -> None:
    tools = build_tools(_tenant(BusinessType.B2C), EnrichmentDeps())
    assert [t.name for t in tools] == ["web_search"]


def test_build_tools_b2c_with_shopify_adds_shopify_tool() -> None:
    class ConnectedDeps(EnrichmentDeps):
        def shopify_connected(self, tenant: Any) -> bool:
            return True

        def shopify_creds(self, tenant: Any) -> ShopifyCreds | None:
            return ShopifyCreds(shop_domain="acme.myshopify.com", access_token="tok")

    tools = build_tools(_tenant(BusinessType.B2C), ConnectedDeps())
    assert [t.name for t in tools] == ["web_search", "shopify_order_history"]


async def test_run_enrichment_returns_validated_result() -> None:
    lead = EnrichmentInput(email="a@b.com", company="Acme", source=LeadSource.EMAIL)
    raw = {
        "company_info": {"industry": "SaaS"},
        "person_info": {},
        "order_history": None,
        "sources": ["https://news/1"],
        "confidence": 0.75,
        "reasoning_trace": "found funding news",
    }
    with patch(
        "modules.enrichment.service.research", new=AsyncMock(return_value=raw)
    ) as mock_research:
        result = await run_enrichment(lead, _tenant(BusinessType.B2B))

    assert isinstance(result, EnrichmentResult)
    assert result.confidence == 0.75
    assert result.sources == ["https://news/1"]
    # web_search was handed to the agent
    passed_tools = mock_research.await_args.kwargs["tools"]
    assert [t.name for t in passed_tools] == ["web_search"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_enrichment_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'modules.enrichment.service'`.

- [ ] **Step 3: Implement the service**

Create `modules/enrichment/service.py`:

```python
"""Public service for lead enrichment.

Assembles a per-tenant toolset (web_search always; Shopify only for B2C tenants
with a connected store), runs the shared research agent toward a lead-enrichment
goal, and returns a validated EnrichmentResult.
"""

from typing import Any

from modules.enrichment.schemas import EnrichmentInput, EnrichmentResult
from modules.enrichment.tools.shopify_order_history import (
    ShopifyCreds,
    ShopifyOrderHistoryTool,
)
from shared.research.agent import research
from shared.research.tools.base import Tool
from shared.research.tools.web_search import web_search
from shared.tenant.schemas import BusinessType

_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "company_info": {"type": "object"},
        "person_info": {"type": "object"},
        "order_history": {"type": "object"},
        "sources": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number"},
        "reasoning_trace": {"type": "string"},
    },
    "required": ["company_info", "person_info", "sources", "confidence", "reasoning_trace"],
}


class EnrichmentDeps:
    """Injected capability lookups. Stubs until the integrations land.

    Subclass and override in tests or once Shopify credential storage exists.
    """

    def shopify_connected(self, tenant: Any) -> bool:
        return False

    def shopify_creds(self, tenant: Any) -> ShopifyCreds | None:
        return None


def build_tools(tenant: Any, deps: EnrichmentDeps) -> list[Tool]:
    """Assemble the toolset for this tenant (Option A: caller decides)."""
    tools: list[Tool] = [web_search]
    if tenant.business_type is BusinessType.B2C and deps.shopify_connected(tenant):
        tools.append(ShopifyOrderHistoryTool(creds=deps.shopify_creds(tenant)))
    return tools


async def run_enrichment(
    lead: EnrichmentInput,
    tenant: Any,
    deps: EnrichmentDeps | None = None,
) -> EnrichmentResult:
    """Enrich a single lead and return a validated EnrichmentResult."""
    deps = deps or EnrichmentDeps()
    tools = build_tools(tenant, deps)
    raw = await research(goal=_build_goal(lead), tools=tools, output_schema=_OUTPUT_SCHEMA)
    return EnrichmentResult.model_validate(raw)


def _build_goal(lead: EnrichmentInput) -> str:
    """Compose the research goal from the lead's known fields."""
    known = {
        "name": lead.name,
        "email": lead.email,
        "phone": lead.phone,
        "company": lead.company,
        **lead.first_party,
    }
    facts = "\n".join(f"- {key}: {value}" for key, value in known.items() if value)
    return (
        "Enrich this inbound lead for sales qualification. Gather company and "
        "person context, and any purchase history available via your tools.\n"
        f"Known lead fields:\n{facts}"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_enrichment_service.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add modules/enrichment/service.py tests/unit/test_enrichment_service.py
git commit -m "feat: add enrichment service with per-tenant toolset assembly"
```

---

## Phase 4 — Onboarding cutover (B1)

### Task 10: Reshape persona to consume researched company info

Change `persona.run` to take a `company_info` dict instead of raw `website_text`.

**Files:**
- Modify: `modules/tenant_onboarding/agents/persona.py`
- Test: `tests/unit/test_onboarding_agents.py`

**Interfaces:**
- Produces: `async def run(company_name: str, business_type: BusinessType, company_info: dict[str, Any]) -> dict[str, Any]`.

- [ ] **Step 1: Update the failing test**

In `tests/unit/test_onboarding_agents.py`, replace the body of `test_persona_agent_returns_business_profile`'s `persona.run(...)` call:

```python
        result = await persona.run(
            company_name="Acme",
            business_type=BusinessType.B2B,
            company_info={"summary": "Acme builds CRM software for small businesses."},
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_onboarding_agents.py::test_persona_agent_returns_business_profile -v`
Expected: FAIL with `TypeError: run() got an unexpected keyword argument 'company_info'`.

- [ ] **Step 3: Reshape persona**

Replace the `run` function in `modules/tenant_onboarding/agents/persona.py` (add `import json` at the top if not present):

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
        f"Researched company info:\n{json.dumps(company_info, indent=2)}\n\n"
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

Add near the existing imports in `persona.py`:

```python
import json
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_onboarding_agents.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add modules/tenant_onboarding/agents/persona.py tests/unit/test_onboarding_agents.py
git commit -m "refactor: persona consumes researched company_info instead of website text"
```

---

### Task 11: Cut the onboarding pipeline over to the research agent

Replace the `httpx` website fetch (and its helpers) with a `research()` call using `web_search`.

**Files:**
- Modify: `modules/tenant_onboarding/pipeline.py`
- Test (replace): `tests/unit/test_onboarding_pipeline.py`

**Interfaces:**
- Consumes: `shared.research.agent.research`, `shared.research.tools.web_search.web_search`.
- Produces: `async def run_pipeline(session: AsyncSession, tenant_id: UUID) -> None` (unchanged signature; new internals). Helpers `_extract_domain`, `_combine_page_texts`, `_strip_html`, `_fallback_fetch` are removed.

- [ ] **Step 1: Replace the pipeline test file**

Replace the entire contents of `tests/unit/test_onboarding_pipeline.py` with:

```python
"""Unit tests for modules/tenant_onboarding/pipeline — all I/O is mocked."""

from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest

from modules.tenant_onboarding.pipeline import run_pipeline
from shared.tenant.schemas import BusinessType, OnboardingStatus


def _mock_tenant(tenant_id: UUID | None = None) -> AsyncMock:
    t = AsyncMock()
    t.id = tenant_id or uuid4()
    t.company_name = "Acme"
    t.business_type = BusinessType.B2B
    t.website_url = "https://acme.com"
    return t


def _config_pieces() -> tuple[list, object, object]:
    from shared.tenant_config.schemas import Dimension, Signal, Thresholds, Weights

    sigs = [Signal(id=f"{d.value.lower()}_1", dimension=d, question="?") for d in Dimension]
    weights = Weights(fit=0.2, intent=0.2, engagement=0.2, behaviour=0.2, context=0.2)
    thresholds = Thresholds(hot=80, warm=55)
    return sigs, weights, thresholds


def _patch_common(monkeypatch: pytest.MonkeyPatch, tenant_id: UUID, set_status: AsyncMock) -> None:
    business_profile = {"industry": "SaaS", "target_market": "SMB"}
    icp_data = {"buyer_role": "VP Sales", "company_size": "50-200"}
    sigs, weights, thresholds = _config_pieces()
    monkeypatch.setattr(
        "modules.tenant_onboarding.pipeline.get_tenant",
        AsyncMock(return_value=_mock_tenant(tenant_id)),
    )
    monkeypatch.setattr(
        "modules.tenant_onboarding.pipeline.set_onboarding_status", set_status
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


async def test_pipeline_happy_path_uses_research_and_sets_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id = uuid4()
    set_status = AsyncMock()
    _patch_common(monkeypatch, tenant_id, set_status)
    research_mock = AsyncMock(return_value={"summary": "Acme sells CRM"})
    monkeypatch.setattr("modules.tenant_onboarding.pipeline.research", research_mock)

    await run_pipeline(AsyncMock(), tenant_id)

    research_mock.assert_awaited_once()
    # persona received the researched company info
    persona_kwargs = research_mock.await_args.kwargs
    assert "goal" in persona_kwargs
    statuses = [c.args[2] for c in set_status.call_args_list]
    assert OnboardingStatus.RUNNING in statuses
    assert OnboardingStatus.COMPLETE in statuses


async def test_pipeline_sets_failed_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant_id = uuid4()
    set_status = AsyncMock()
    monkeypatch.setattr(
        "modules.tenant_onboarding.pipeline.get_tenant",
        AsyncMock(return_value=_mock_tenant(tenant_id)),
    )
    monkeypatch.setattr(
        "modules.tenant_onboarding.pipeline.set_onboarding_status", set_status
    )
    monkeypatch.setattr(
        "modules.tenant_onboarding.pipeline.research",
        AsyncMock(side_effect=Exception("research failed")),
    )

    with pytest.raises(Exception, match="research failed"):
        await run_pipeline(AsyncMock(), tenant_id)

    statuses = [c.args[2] for c in set_status.call_args_list]
    assert OnboardingStatus.FAILED in statuses


async def test_pipeline_passes_web_search_tool_to_research(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id = uuid4()
    _patch_common(monkeypatch, tenant_id, AsyncMock())
    research_mock = AsyncMock(return_value={"summary": "info"})
    monkeypatch.setattr("modules.tenant_onboarding.pipeline.research", research_mock)

    await run_pipeline(AsyncMock(), tenant_id)

    tools = research_mock.await_args.kwargs["tools"]
    assert [t.name for t in tools] == ["web_search"]


async def test_pipeline_tags_trace_with_tenant_id(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant_id = uuid4()
    _patch_common(monkeypatch, tenant_id, AsyncMock())
    monkeypatch.setattr(
        "modules.tenant_onboarding.pipeline.research",
        AsyncMock(return_value={"summary": "info"}),
    )

    with patch("modules.tenant_onboarding.pipeline.langfuse_context") as mock_ctx:
        await run_pipeline(AsyncMock(), tenant_id)

    mock_ctx.update_current_trace.assert_called_once_with(
        name="tenant-onboarding",
        metadata={"tenant_id": str(tenant_id)},
        tags=[str(tenant_id)],
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_onboarding_pipeline.py -v`
Expected: FAIL with `ImportError: cannot import name 'research'` (pipeline does not import it yet).

- [ ] **Step 3: Rewrite the pipeline**

Replace the entire contents of `modules/tenant_onboarding/pipeline.py` with:

```python
"""Pipeline: researches the tenant's company, then runs the three agents."""

from uuid import UUID

from langfuse.decorators import langfuse_context, observe
from sqlalchemy.ext.asyncio import AsyncSession

from modules.tenant_onboarding.agents import icp, persona, signals
from shared.research.agent import research
from shared.research.tools.web_search import web_search
from shared.tenant.schemas import OnboardingStatus
from shared.tenant.service import activate_tenant, get_tenant, set_onboarding_status
from shared.tenant_config.schemas import TenantConfigCreate
from shared.tenant_config.service import create_active

_COMPANY_INFO_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "industry": {"type": "string"},
        "products_services": {"type": "string"},
        "target_market": {"type": "string"},
        "notable_facts": {"type": "string"},
    },
    "required": ["summary", "industry", "products_services", "target_market", "notable_facts"],
}


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
            goal=_company_goal(tenant.company_name, str(tenant.website_url)),
            tools=[web_search],
            output_schema=_COMPANY_INFO_SCHEMA,
        )

        business_profile = await persona.run(
            company_name=tenant.company_name,
            business_type=tenant.business_type,
            company_info=company_info,
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


def _company_goal(company_name: str, website_url: str) -> str:
    """Compose the research goal for gathering company info during onboarding."""
    return (
        f"Gather a factual profile of the company '{company_name}' "
        f"(website: {website_url}): what it does, its products/services, its "
        f"target market, and any notable facts. Search the web to confirm."
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_onboarding_pipeline.py -v`
Expected: PASS.

- [ ] **Step 5: Full gate + commit**

Run: `make ci`
Expected: lint, typecheck, and the full test suite all pass.

```bash
git add modules/tenant_onboarding/pipeline.py tests/unit/test_onboarding_pipeline.py
git commit -m "feat: cut onboarding pipeline over to the shared research agent"
```

---

## Self-Review

**Spec coverage:**
- Reusable agent in `shared/research` → Tasks 3–5. ✅
- `web_search` tool → Task 4. ✅
- `shopify_order_history` tool (interface, integration deferred) → Task 8. ✅
- Lead enrichment with per-tenant toolset (Option A) → Task 9. ✅
- Onboarding cutover (B1) → Tasks 10–11. ✅
- Multi-turn tool-calling in `groq_client`, Langfuse-traced per turn → Task 2. ✅
- Fattened `shared/events` contracts (event-carried state) → Task 6. ✅
- Error handling (tool failures surfaced to the model, loop continues) → Task 5 (`dispatch` catches `ExternalServiceError`) + tests. ✅
- Deferred items (real Shopify API, enrichment event/worker wiring, persistence) are intentionally **not** tasks here — see spec "Deferred". Enrichment's `run_enrichment` is callable and unit-tested but not yet triggered by a worker/event (needs `lead_ingestion`).

**Placeholder scan:** No TBD/TODO or vague "add error handling" steps — every code step shows complete code. (The Shopify tool's deferred body is an explicit, tested `NotImplementedError`, not a placeholder.)

**Type consistency:** `Tool` protocol (`name`/`description`/`parameters`/`run(**kwargs)`) is used consistently by `WebSearchTool`, `ShopifyOrderHistoryTool`, and the test fakes. `research(goal, tools, output_schema)` keywords match all call sites (Task 9 service, Task 11 pipeline). `run_tool_loop(messages, tools, dispatch, max_iterations)` matches the agent's call in Task 5. `EnrichmentResult` fields (`company_info`, `person_info`, `order_history`, `sources`, `confidence`, `reasoning_trace`) match the service's `_OUTPUT_SCHEMA` and the Task 6 model.

**Note on `make ci`:** run it at the end of Task 11 (it is the first task that leaves the whole repo in its final shape). If mypy flags the `EnrichmentInput = LeadPayload` alias export, keep the alias but ensure `modules/enrichment/schemas.py` lists it in `__all__` (already done).
