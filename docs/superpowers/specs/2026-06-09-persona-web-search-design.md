# Persona Agent — SerpAPI Web Search Enhancement

**Date:** 2026-06-09
**Status:** Approved

## Problem

The tenant onboarding pipeline currently fetches only the homepage of the tenant's website. This gives the persona agent limited content — a homepage rarely contains the full picture of a company's products, services, team, and value proposition.

## Goal

Automatically discover and fetch the 5 most relevant pages on a company's website using SerpAPI, combine their content, and feed it to the persona agent for a richer business profile — all without any additional manual input from the tenant.

## Approach

Option A: new `clients/serpapi_client.py` + changes to `pipeline.py` only. `persona.py` interface is unchanged.

## Design

### 1. `clients/serpapi_client.py`

Thin async HTTP wrapper around SerpAPI's JSON API. Single public function:

```python
async def search_site_pages(domain: str, num: int = 5) -> list[str]
```

- Queries SerpAPI with `q=site:{domain}`, `num=num`, `api_key=settings.serpapi_api_key`
- Returns a list of up to `num` result URLs
- Raises `ExternalServiceError` on API failure or non-200 response
- Uses `httpx.AsyncClient` for the HTTP call

### 2. `core/config.py`

Add one new field:

```python
serpapi_api_key: str = ""
```

Empty default keeps tests and local imports working (same pattern as `groq_api_key`).

### 3. `pipeline.py` changes

Replace the single homepage fetch:

```python
# Before
response = await http.get(str(tenant.website_url))
website_text = _strip_html(response.text)

# After
domain = _extract_domain(tenant.website_url)
urls = await search_site_pages(domain, num=5)
pages = await asyncio.gather(*[http.get(u) for u in urls], return_exceptions=True)
website_text = _combine_page_texts(pages) or _fallback_fetch(http, tenant.website_url)
```

Two new private helpers in `pipeline.py`:

- `_extract_domain(url)` — strips scheme and path, returns bare domain (e.g. `example.com`)
- `_combine_page_texts(results)` — iterates gather results, skips exceptions and non-200 responses, strips HTML from each, joins with `\n\n`, returns combined string (empty string if all failed)

Fallback: if SerpAPI returns no URLs, or `_combine_page_texts` returns empty, fetch `website_url` directly (current behaviour).

### 4. `persona.py`

No changes. Receives `website_text` exactly as before — just richer content.

## Error Handling

| Scenario | Behaviour |
|---|---|
| SerpAPI returns no results | Fall back to direct homepage fetch |
| All 5 page fetches fail | Fall back to direct homepage fetch |
| Some pages fail, some succeed | Use text from successful pages only |
| SerpAPI API key missing | `ExternalServiceError` propagates → `onboarding_status = FAILED` |

## Data Flow

```
POST /onboarding
  → pipeline.run_pipeline()
      → search_site_pages(domain)          # SerpAPI: site:domain.com → 5 URLs
      → asyncio.gather(*[http.get(url)])   # fetch all 5 in parallel
      → _combine_page_texts(results)       # strip HTML, join
      → persona.run(website_text=combined) # unchanged interface
      → icp.run(...)
      → signals.run(...)
      → create_active(...)
      → activate_tenant(...)
```

## Configuration

Add `SERPAPI_API_KEY` to `.env` and `.env.example`.

## Testing

- **Unit:** mock `search_site_pages` and `httpx` — test `_combine_page_texts` with mixed success/failure results, test `_extract_domain`, test fallback logic
- **Integration:** real SerpAPI call against a known domain (skipped in CI if key absent)
