"""Unit tests for clients/serper_client — mocks httpx."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clients.serper_client import search_site_pages
from core.exceptions import ExternalServiceError


def _make_response(status: int, body: dict[str, Any]) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body
    return r


async def test_returns_urls_from_organic_results() -> None:
    links = [f"https://example.com/page{i}" for i in range(5)]
    body = {"organic": [{"link": link} for link in links]}
    mock_resp = _make_response(200, body)

    with patch("clients.serper_client.httpx.AsyncClient") as mock_cls:
        mock_post = AsyncMock(return_value=mock_resp)
        mock_cls.return_value.__aenter__.return_value.post = mock_post
        urls = await search_site_pages("example.com", num=5)

    assert urls == links
    call_kwargs = mock_post.call_args
    assert call_kwargs is not None
    called_url = call_kwargs.args[0] if call_kwargs.args else call_kwargs.kwargs.get("url", "")
    called_json = call_kwargs.kwargs.get("json", {})
    assert "serper.dev" in called_url
    assert called_json.get("q") == "site:example.com"
    assert called_json.get("num") == 5


async def test_respects_num_limit() -> None:
    links = [f"https://example.com/page{i}" for i in range(10)]
    body = {"organic": [{"link": link} for link in links]}
    mock_resp = _make_response(200, body)

    with patch("clients.serper_client.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_resp)
        urls = await search_site_pages("example.com", num=3)

    assert len(urls) == 3


async def test_returns_empty_list_when_no_organic_results() -> None:
    mock_resp = _make_response(200, {"organic": []})

    with patch("clients.serper_client.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_resp)
        urls = await search_site_pages("example.com")

    assert urls == []


async def test_returns_empty_list_when_key_missing_from_response() -> None:
    mock_resp = _make_response(200, {})

    with patch("clients.serper_client.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_resp)
        urls = await search_site_pages("example.com")

    assert urls == []


async def test_raises_external_service_error_on_non_200() -> None:
    mock_resp = _make_response(401, {})

    with patch("clients.serper_client.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_resp)
        with pytest.raises(ExternalServiceError, match="Serper returned 401"):
            await search_site_pages("example.com")


async def test_raises_external_service_error_on_network_failure() -> None:
    with patch("clients.serper_client.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__.return_value.post = AsyncMock(
            side_effect=Exception("connection refused")
        )
        with pytest.raises(ExternalServiceError, match="Serper request failed"):
            await search_site_pages("example.com")
