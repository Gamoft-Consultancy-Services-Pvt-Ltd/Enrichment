"""Thin async wrapper around the Serper Google Search API."""

from typing import Any

import httpx

from core.config import get_settings
from core.exceptions import ExternalServiceError

_SERPER_URL = "https://google.serper.dev/search"


async def search_site_pages(domain: str, num: int = 5) -> list[str]:
    """Return up to `num` page URLs for `site:<domain>` from Serper.

    Returns an empty list if Serper has no organic results.
    Raises ExternalServiceError on HTTP errors or network failures.
    """
    headers = {
        "X-API-KEY": get_settings().serper_api_key,
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {
        "q": f"site:{domain}",
        "num": num,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            response = await http.post(_SERPER_URL, headers=headers, json=payload)
    except Exception as exc:
        raise ExternalServiceError(f"Serper request failed: {exc}") from exc

    if response.status_code != 200:
        raise ExternalServiceError(f"Serper returned {response.status_code}")

    data: dict[str, Any] = response.json()
    results: list[dict[str, Any]] = data.get("organic", [])
    return [r["link"] for r in results[:num]]
