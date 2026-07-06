# Enrichment Agent — LangGraph + MCP Design

**Date:** 2026-07-06
**Status:** Approved (supersedes `2026-07-02-enrichment-agent-design.md`)

## Summary

Build a reusable web-research agent on **LangGraph** whose single `web_search`
tool is served by a **standalone Serper MCP server**. The agent is reused by
**lead enrichment** and **tenant onboarding**. Shopify order history is **not**
an agent tool — it is a deterministic, non-LLM fetch attached to the enrichment
result and left to the scoring agent to interpret. Cross-module data flows via
**fattened `shared/events` contracts** (event-carried state).

This supersedes the earlier hand-rolled ReAct design. The change was driven by
three decisions taken during brainstorming: use LangGraph (not a hand-rolled
loop), serve tools from an MCP server (not in-process `Tool` objects), and keep
Shopify out of the agent's hands entirely (a rule, not a tool).

## Goals

- One research agent, reused by `modules/enrichment` and `modules/tenant_onboarding`.
- The agent composes its own search queries, reads results, decides sufficiency,
  loops to a hard cap, and returns a structured result via LangGraph's native
  `response_format`.
- Web search is provided by a standalone MCP server (streamable-http). The Serper
  API key lives only in that server.
- Shopify order history is fetched deterministically (never by the LLM, so
  per-tenant credentials never enter the model context or traces) and attached to
  the enrichment result. **Its output is PII-free** — order facts only, never
  customer email/phone/name/address.
- Respect the dependency rule: `mcp_servers/ → clients`; `shared/research`
  imports only `clients` + `core` + third-party, never a module; both consuming
  modules import `shared/research`.

## Non-goals (deferred)

- The real Shopify API call inside `fetch_order_history` (stub raises
  `NotImplementedError` now).
- The `ChannelConnection` lookup + credential decryption that populates
  `EnrichmentDeps.shopify_creds`.
- The orchestration consumer / ARQ worker that invokes `run_enrichment` on
  `LeadReceived` and emits `LeadEnriched` (needs `modules/orchestration`).
- Persisting enrichment results to a table.
- Pooling a long-lived MCP client (perf optimization).

## Architecture

Reusable research agent (LangGraph) → gets `web_search` from a standalone Serper
MCP server → reused by enrichment and onboarding. Shopify order history is a
separate deterministic fetch.

| Component | Layer | Responsibility |
|---|---|---|
| `mcp_servers/web_search/server.py` | entry point (new) | Standalone MCP server (streamable-http). Exposes `web_search(query, num)` → `clients.serper_client.search`. Holds the Serper key. |
| `clients/serper_client.search()` | clients | Query → LLM-readable digest. |
| `clients/groq_client.get_chat_model()` | clients | Factory → configured `ChatGroq` (model, temp 0, key). `call_with_tool` unchanged. |
| `shared/research/agent.py` | shared | `research(*, goal, output_schema) -> <schema instance>`. Connects to MCP, builds `create_react_agent(model, tools, response_format=output_schema)`, runs it, returns the structured result. Langfuse via callback. Imports no module. |
| `shared/events/schemas.py` | shared | Add `LeadPayload`, `EnrichmentResult`; fatten `LeadReceived.payload` (required) + `LeadEnriched.result`. |
| `modules/enrichment/service.py` | module | `run_enrichment(lead, tenant, deps) -> EnrichmentResult`. |
| `modules/enrichment/tools/shopify_order_history.py` | module | `fetch_order_history(creds, *, email, phone) -> dict` — PII-free. Stub now. |
| `modules/enrichment/schemas.py` | module | `ResearchFindings`, `EnrichmentDeps`, `ShopifyCreds`, `EnrichmentInput` alias, re-export `EnrichmentResult`. |
| `modules/lead_ingestion/pipeline.py` | module | Populate `LeadReceived.payload` (Option A). |
| `modules/tenant_onboarding/` | module | `pipeline.py` + `agents/persona.py` cut over to `research()`; drop httpx fetch. |
| `core/config.py`, `docker-compose.yml` | core / infra | `mcp_web_search_url` setting; `mcp-web-search` service. |

**Dependency-rule check:** `mcp_servers/` sits at the top (imports `clients`,
like `api`/`workers`). `shared/research` imports only `clients` + `core` +
third-party (langgraph, langchain-mcp-adapters, langfuse). Both `enrichment` and
`onboarding` import `shared/research` — reuse without module-to-module imports.

**Data flow (end state):**

```
LeadReceived(payload)
   └─[future orchestration consumer]→ run_enrichment(lead, tenant, deps)
          ├─ research(goal, ResearchFindings) ──→ web_search MCP server ──→ Serper
          └─ if deps.shopify_creds: fetch_order_history(creds, email/phone)  [deterministic, PII-free]
                 └─→ EnrichmentResult ──→ LeadEnriched(result) ──→ scoring
```

## Component detail

### MCP web_search server (`mcp_servers/web_search/server.py`)

FastMCP over streamable-http. Package `mcp_servers/` is laid out to hold sibling
servers later, but `web_search` is self-contained now (no premature framework).

```python
mcp = FastMCP("web-search")

@mcp.tool()
async def web_search(query: str, num: int = 5) -> str:
    """Search the web and return a readable digest of the top results."""
    return await serper_client.search(query, num=num)

# transport="streamable-http", path="/mcp", host 0.0.0.0, port from env
```

The tool docstring is the description the LLM reads. Serper key from settings/env.

### Groq chat-model factory (`clients/groq_client.py`)

Added beside the untouched `call_with_tool`:

```python
def get_chat_model() -> ChatGroq:
    return ChatGroq(model=_MODEL, temperature=0.0, api_key=get_settings().groq_api_key)
```

### Research agent (`shared/research/agent.py`)

```python
async def research(*, goal: str, output_schema: type[T]) -> T:
    """Run the web-research agent to `goal` and return `output_schema` filled in."""
    async with MultiServerMCPClient(
        {"web_search": {"url": get_settings().mcp_web_search_url,
                        "transport": "streamable_http"}}
    ) as client:
        tools = await client.get_tools()
        agent = create_react_agent(get_chat_model(), tools, response_format=output_schema)
        state = await agent.ainvoke(
            {"messages": [SystemMessage(_SYSTEM), HumanMessage(goal)]},
            config={"recursion_limit": _RECURSION_LIMIT, "callbacks": [_langfuse_handler()]},
        )
    return cast(T, state["structured_response"])
```

- `response_format=output_schema` (a pydantic model type) → LangGraph runs a final
  structured-output pass and stores the instance in `state["structured_response"]`.
- `recursion_limit` is the hard cap on the ReAct loop.
- `_SYSTEM` instructs the agent to compose its own queries, read results, search
  again if unsure, and stop when it has enough to fill the schema. `goal` is
  caller-supplied (enrichment vs onboarding differ).
- Langfuse via callback handler in `config` (replaces `@observe` for the loop).
- `research()` knows nothing about enrichment or onboarding.
- Errors (MCP unreachable, agent failure, structured-output failure) wrap in
  `core.exceptions.ExternalServiceError`.

### Event contracts (`shared/events/schemas.py`)

```python
class LeadPayload(BaseModel):          # rides on LeadReceived (event-carried state)
    name / email / phone / company: str | None = None
    source: LeadSource
    first_party: dict[str, Any] = {}

class EnrichmentResult(BaseModel):     # rides on LeadEnriched → scoring
    company_info: dict[str, Any] = {}
    person_info: dict[str, Any] = {}
    order_history: dict[str, Any] | None = None
    sources: list[str] = []
    confidence: float                  # 0..1
    reasoning_trace: str = ""

LeadReceived.payload: LeadPayload      # required
LeadEnriched.result: EnrichmentResult
```

`lead_ingestion` populates `payload` from its `NormalisedChannelEvent`
(`name=full_name, email, phone, source, first_party=extra_fields`). Its
integration tests assert only `lead_id`/`tenant_id`/`source` and drive the real
pipeline, so they stay green.

### Enrichment schemas (`modules/enrichment/schemas.py`)

```python
class ResearchFindings(BaseModel):     # the agent's response_format output
    company_info: dict[str, Any] = {}
    person_info: dict[str, Any] = {}
    sources: list[str] = []
    confidence: float
    reasoning_trace: str = ""

class ShopifyCreds(BaseModel): shop_domain: str; access_token: str
class EnrichmentDeps(BaseModel): shopify_creds: ShopifyCreds | None = None
EnrichmentInput = LeadPayload          # alias; re-export EnrichmentResult
```

`ResearchFindings` is `EnrichmentResult` minus `order_history` — the agent
produces the web-research half; the service adds order history.

### Shopify order-history fetch (`modules/enrichment/tools/shopify_order_history.py`)

```python
async def fetch_order_history(creds: ShopifyCreds, *, email, phone) -> dict[str, Any]:
    """Return PII-free order facts for a customer. Real Shopify call deferred."""
    raise NotImplementedError("Shopify order-history integration pending")
```

Output shape when built: `{customer_found, order_count, total_spent, currency,
orders:[{id, created_at, total, line_items:[{title, qty, price}]}]}` — no
email/phone/name/address. Called only when `deps.shopify_creds` is present.

### Enrichment service (`modules/enrichment/service.py`)

```python
async def run_enrichment(lead: LeadPayload, tenant: TenantRead,
                         deps: EnrichmentDeps | None = None) -> EnrichmentResult:
    goal = _build_goal(lead, tenant.business_type)     # B2B: company-focused; B2C: lighter
    findings = await research(goal=goal, output_schema=ResearchFindings)
    order_history = None
    if deps and deps.shopify_creds:                    # deterministic, non-agent
        order_history = await fetch_order_history(deps.shopify_creds,
                                                  email=lead.email, phone=lead.phone)
    return EnrichmentResult(**findings.model_dump(), order_history=order_history)
```

- Both business types run `research()` (hybrid); only the **goal** differs.
- Order history is added only when creds are present. Since creds-populating is
  deferred, today's live path yields `order_history=None`; the `NotImplementedError`
  marks where the real call lands.
- `tenant: TenantRead` from `shared/tenant`; service reads `business_type` only.

### Onboarding cutover (`modules/tenant_onboarding/`)

- `pipeline.py`: replace httpx fetch + HTML-strip with `research()`. Define a
  `CompanyInfo` schema (`summary, industry, products_services, target_market,
  notable_facts`):
  ```python
  company_info = await research(goal=_company_goal(tenant.website_url, tenant.company_name),
                                output_schema=CompanyInfo)
  ```
  Delete `_strip_html`, `_combine_page_texts`, `_extract_domain`, `_fallback_fetch`,
  and the httpx import.
- `agents/persona.py`: reshape `run(company_name, business_type, company_info: dict)
  -> dict`. ICP and Signals agents downstream unchanged.
- Onboarding uses the same MCP `web_search` as enrichment — the reuse goal.

## Config / deploy / dependencies

- `core/config.py`: add `mcp_web_search_url: str` (default `http://localhost:8000/mcp`).
- `docker-compose.yml`: new `mcp-web-search` service (same image, runs the server
  module, gets `SERPER_API_KEY`); `app` + `worker` get
  `MCP_WEB_SEARCH_URL=http://mcp-web-search:8000/mcp`.
- New deps: `langgraph`, `langchain-groq`, `langchain-mcp-adapters`, `mcp`
  (FastMCP). `langfuse` already present.

## Testing (TDD, unit tier — no network/DB)

- `serper.search` → mock httpx.
- MCP `web_search` tool → mock `serper.search`, assert pass-through.
- `research()` → patch `MultiServerMCPClient` + `create_react_agent` in
  `shared.research.agent`; fake agent returns `{"structured_response": <instance>}`;
  assert goal/schema wired; mock langfuse handler.
- `get_chat_model()` → assert `ChatGroq` built with temp 0 + model.
- `run_enrichment` → patch `research` + `fetch_order_history`; assert goal varies
  by `business_type`, order history attached only when creds present.
- `fetch_order_history` → asserts `NotImplementedError` (deferred boundary).
- events → schema tests (payload required, result carried, confidence range).
- `lead_ingestion` → existing integration tests stay green (payload populated).
- onboarding → patch `research` + persona.
- MCP server round-trip: light integration test optional (start FastMCP
  in-process); unit tier mocks the client.

Run `make ci` (lint + typecheck + test) at the end.

## Key decisions

- **LangGraph over hand-rolled loop.** `research(goal, output_schema)` is a
  framework-agnostic seam; internals can change without touching callers.
- **Native `response_format`** for structured output (LangGraph's built-in final
  pass), not a separate `call_with_tool` extraction.
- **Standalone MCP server (streamable-http)** for tools, not in-process `Tool`
  objects. Deliberately microservice-style; moves off strict single-deployable.
- **Shopify as a deterministic rule, not an agent tool** — resolves the
  per-tenant-secret-through-the-LLM problem and keeps output PII-free.
- **Event-carried state (Option A)** — `LeadReceived.payload` required; wire
  `lead_ingestion` to populate it now.
