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
    body = {"organic_results": [{"link": link} for link in links]}
    mock_resp = _make_response(200, body)

    with patch("clients.serpapi_client.httpx.AsyncClient") as mock_cls:
        mock_get = AsyncMock(return_value=mock_resp)
        mock_cls.return_value.__aenter__.return_value.get = mock_get
        urls = await search_site_pages("example.com", num=5)

    assert urls == links
    call_kwargs = mock_get.call_args
    assert call_kwargs is not None
    called_url = call_kwargs.args[0] if call_kwargs.args else call_kwargs.kwargs.get("url", "")
    called_params = call_kwargs.kwargs.get("params", {})
    assert "serpapi.com" in called_url
    assert called_params.get("q") == "site:example.com"
    assert called_params.get("num") == 5
    assert called_params.get("engine") == "google"


async def test_respects_num_limit() -> None:
    links = [f"https://example.com/page{i}" for i in range(10)]
    body = {"organic_results": [{"link": link} for link in links]}
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
