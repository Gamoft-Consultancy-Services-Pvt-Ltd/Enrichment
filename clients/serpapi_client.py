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
