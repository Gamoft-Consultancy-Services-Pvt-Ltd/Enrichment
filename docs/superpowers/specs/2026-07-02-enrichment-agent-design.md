# Enrichment agent — design

**Date:** 2026-07-02
**Status:** Approved (pending spec review)
**Supersedes:** the `enrichment-websearch-redesign` planning note (the placement
question it deferred is resolved here).

## Summary

Build a **self-querying web-research agent** plus a **Shopify order-history tool**
and wire them into lead enrichment. The research agent is reusable, so it lives in
`shared/research` and is adopted by **both** `modules/enrichment` (lead enrichment)
and `modules/tenant_onboarding` (tenant/company enrichment, replacing today's
direct `httpx` website fetch) in this same effort (sequencing option **B1**).

The agent is an LLM ReAct loop: it **writes its own search queries**, reads the
returned snippets + knowledge graph, judges relevance/sufficiency, and loops
(hard iteration cap) until it has enough — then emits a structured result matching
a caller-supplied output schema. Temperature 0; every iteration traced in Langfuse.

## Goals / non-goals

**Goals**
- A reusable, tool-injectable research agent in `shared/research`.
- A `web_search` tool (Serper-backed) usable by any caller.
- A `shopify_order_history` tool (interface complete; real API call deferred).
- Lead enrichment (`modules/enrichment`) that assembles a per-tenant toolset and
  produces a structured `EnrichmentResult`.
- Cut `modules/tenant_onboarding` over to the shared agent.
- Multi-turn tool-calling capability in `clients/groq_client`, Langfuse-traced.

**Non-goals (deferred — see "Deferred" below)**
- Real Shopify API integration + credential storage.
- Enrichment's event/worker wiring (needs the unbuilt `lead_ingestion`).
- Persisting enrichment results (provenance table).

## Architecture & placement

Placement is forced by three facts the user chose to keep: (a) it is the *same*
code, (b) *two* modules call it, (c) the rule "no module imports another module".
The only layers both modules may import are `shared/`, `clients/`, `core/` — so the
reusable agent lives in `shared/research`; only lead-specific pieces stay in
`modules/enrichment`.

```
clients/groq_client.py
  run_tool_loop(...)          NEW: multi-turn tool-calling, Langfuse-traced per turn
  call_with_tool(...)         unchanged (single-shot)
clients/serper_client.py      exists; add a query-based search if needed

shared/research/              imported by BOTH modules
  tools/base.py               Tool protocol: name, description, arg schema, async run()
  tools/web_search.py         web_search tool over clients/serper_client
  agent.py                    research(goal, tools, output_schema) -> dict  (ReAct loop)

modules/enrichment/           lead-specific, cohesive
  service.py                  run_enrichment(input, tenant, deps) -> EnrichmentResult
  schemas.py                  EnrichmentInput, EnrichmentResult
  tools/shopify_order_history.py   Shopify tool (stub body, frozen interface)

modules/tenant_onboarding/
  pipeline.py                 replace httpx fetch with shared research() call
  agents/persona.py           reshape input: consumes researched company info

shared/events/schemas.py      contracts (fattened — see below)
```

Dependency direction stays intact: `modules → shared → clients → core`. No
module imports another module; no `shared → module` inversion.

## The research agent (`shared/research/agent.py`)

`research(goal: str, tools: list[Tool], output_schema: dict) -> dict`

Loop, each turn:
1. LLM is given the goal, the available tools, and the results so far.
2. It either calls a tool (e.g. `web_search(query="...")` — **it composes the
   query itself**) or decides it has enough.
3. Tool result is appended to the conversation; loop continues.
4. Stop when the LLM signals sufficiency **or** the hard iteration cap is hit.
5. A final forced structured call returns a dict matching `output_schema`.

The agent is **generic over output**: each caller passes its own `output_schema`
(enrichment → `EnrichmentResult`; onboarding → business-profile schema) and its
own toolset. Temperature 0; cost-aware ("stop when confident"); Langfuse traces
each iteration via `run_tool_loop`.

### The `Tool` protocol (`shared/research/tools/base.py`)

Each tool exposes: `name`, `description`, a JSON-Schema for its arguments, and an
async `run(**args) -> str` (result serialized back into the conversation). The
agent knows only this protocol — that is what lets callers inject any toolset.

## `web_search` tool (`shared/research/tools/web_search.py`)

- Arg schema: `{query: string}`. The **agent** supplies the query — the tool is a
  dumb pipe to Serper (organic snippets + knowledge graph), not a searcher.
- Wraps `clients/serper_client`. Raises `ExternalServiceError` on HTTP/network
  failure (caught by the agent, see Error handling).

## `shopify_order_history` tool (`modules/enrichment/tools/shopify_order_history.py`)

Interface built now; real integration deferred.

- `run(email, phone, creds) -> dict` returning **raw orders** (capped to the most
  recent N to bound tokens):
  ```
  {
    "customer_found": bool,
    "orders": [
      {"id", "created_at", "total", "currency",
       "line_items": [{"title", "qty", "price"}]},
      ...
    ]
  }
  ```
- Lookup key: `email` primary, `phone` fallback.
- Body is a stub until `clients/shopify_client` exists: returns
  `customer_found=False` / raises a clear "Shopify integration not configured".
  The interface is frozen so the agent and its tests are complete now.

## Toolset assembly — Option A (`modules/enrichment/service.py`)

The **caller** assembles the toolset per tenant (matches "match tools to the
tenant's profile, skip layers it doesn't need"):

```
tools = [web_search]                          # every tenant
if tenant.business_type is BusinessType.B2C and deps.shopify_connected(tenant):
    tools.append(shopify_order_history)       # only real Shopify stores
```

`tenant.business_type` exists today. `shopify_connected` is a stub gate (defaults
False; forceable in tests) until the Shopify integration lands. The LLM then
freely chooses among whatever tools it was given.

## Data flow & contracts

Cross-module data moves by **event-carried state (Option A)** — no module reads
another module's tables:

- **`LeadReceived`** (in `shared/events`) is fattened to carry the normalized lead
  payload (name, email, phone, company, source, first-party fields). Enrichment
  works purely off the event; it never reads `lead_ingestion`'s tables.
- **`LeadEnriched`** is fattened to carry the `EnrichmentResult` (company_info,
  person_info, order-history findings, `sources[]`, `confidence`,
  `reasoning_trace`) so `scoring` consumes it by event too.

`EnrichmentInput` mirrors the `LeadReceived` payload; `EnrichmentResult` is the
`LeadEnriched` payload. (Contracts may exist ahead of a live bus — `shared/events`
is already contracts-only.)

End-to-end (once wiring lands): `lead_ingestion` emits `LeadReceived` →
enrichment agent runs (web_search [+ shopify]) → `EnrichmentResult` →
`LeadEnriched` → scoring.

## `clients/groq_client.run_tool_loop`

New multi-turn function beside the existing single-shot `call_with_tool` (which is
untouched). It passes many tools with `tool_choice="auto"`, executes the model's
chosen tool via a caller-supplied dispatch, appends the result as a tool message,
and calls again — until the model stops or a cap is reached. Same Langfuse
`@observe(as_type="generation")` treatment so **each iteration is a traced
generation**. Provider-specific plumbing, hence `clients/` (tool-agnostic — it
takes tool definitions + a dispatch callback, not enrichment logic).

## Onboarding cutover (B1)

`modules/tenant_onboarding/pipeline.py` currently: Serper `site:` URLs → `httpx`
page fetch → `_strip_html` → `persona.run(website_text=...)` → icp → signals.

After: build a research goal ("gather company info for `<company>`, `<domain>`"),
call `shared.research.research(goal, tools=[web_search], output_schema=...)`, and
feed the researched company info into `persona`. The direct `httpx` fetch and
`_strip_html`/`_fallback_fetch` helpers are removed (this is the SSRF/flaky-HTML
risk the redesign note called out). `icp` and `signals` are unchanged.

## Error handling

- A tool failure (Serper down, Shopify not configured) is caught by the agent,
  surfaced back to the LLM as a tool-error message, and the loop continues with
  the remaining tools rather than crashing — enrichment degrades gracefully.
- If the loop hits the cap with thin data, it still returns a valid result with
  low `confidence` and whatever `sources` it gathered (calibration rule: a
  partial result beats nothing).
- Client wrappers raise `ExternalServiceError`, consistent with existing code.

## Testing (TDD, unit-only, no DB/network)

- `shared/research/agent.py`: fake in-memory tools + stubbed `run_tool_loop` →
  assert loop control (query→search→evaluate, tool selection, iteration cap,
  final structured output, tool-error recovery).
- `shared/research/tools/web_search.py`: mock `serper_client` → assert `{query}`
  arg schema + result shaping + error propagation.
- `modules/enrichment/tools/shopify_order_history.py`: assert raw-orders shape,
  email/phone lookup, and the not-configured stub path.
- `modules/enrichment/service.py`: assert Option-A toolset assembly across
  B2B / B2C / shopify-connected combinations.
- `clients/groq_client.run_tool_loop`: mock `AsyncGroq` → assert multi-turn
  message threading + a Langfuse observation per iteration.
- `modules/tenant_onboarding/pipeline.py`: assert it calls `research()` instead of
  fetching, and that persona receives the researched info; update existing
  pipeline tests.

`mypy` strict over the whole repo (incl. tests); `ruff` line length 100.

## Deferred (tracked, not built now)

1. **Shopify real integration** — `clients/shopify_client.py` + credential storage
   + flipping `shopify_connected` to a real check. (User's stated next step.)
2. **Enrichment event/worker wiring** — consuming `LeadReceived`, emitting
   `LeadEnriched`, ARQ job — needs `lead_ingestion`. Until then `service.run_enrichment`
   is callable and unit-tested but not triggered in production.
3. **Persistence** — an enrichment-results table for provenance (ADR 0003), added
   with the wiring.

## Open questions

None blocking. Iteration cap value and the recent-orders cap `N` are tunable
defaults chosen during implementation.
