"""Thin async wrapper around the Serper Google Search API."""

from typing import Any

import httpx

from core.config import get_settings
from core.exceptions import ExternalServiceError

_SERPER_URL = "https://google.serper.dev/search"


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
