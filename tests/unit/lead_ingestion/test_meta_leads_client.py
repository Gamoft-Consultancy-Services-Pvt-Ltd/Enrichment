"""Unit tests for clients/meta_leads_client.py — no DB, no real network."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clients.meta_leads_client import fetch_lead_fields
from core.exceptions import ExternalServiceError


def _settings() -> MagicMock:
    s = MagicMock()
    s.meta_graph_api_version = "v21.0"
    return s


async def test_fetch_lead_fields_returns_field_data_list() -> None:
    field_data: list[dict[str, Any]] = [
        {"name": "full_name", "values": ["Rahul Sharma"]},
        {"name": "email", "values": ["rahul@example.com"]},
    ]
    mock_response = MagicMock()
    mock_response.is_success = True
    mock_response.json.return_value = {"id": "leadgen-123", "field_data": field_data}

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await fetch_lead_fields("leadgen-123", "page-token", settings=_settings())

    assert result == field_data
    call_kwargs = mock_client.get.call_args
    assert "leadgen-123" in call_kwargs[0][0]
    params = call_kwargs[1]["params"]
    assert params["fields"] == "field_data"
    assert params["access_token"] == "page-token"


async def test_fetch_lead_fields_uses_graph_api_version() -> None:
    s = _settings()
    s.meta_graph_api_version = "v22.0"

    mock_response = MagicMock()
    mock_response.is_success = True
    mock_response.json.return_value = {"field_data": []}

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        await fetch_lead_fields("lg-1", "tok", settings=s)

    url = mock_client.get.call_args[0][0]
    assert "v22.0" in url


async def test_fetch_lead_fields_empty_field_data_returns_empty_list() -> None:
    mock_response = MagicMock()
    mock_response.is_success = True
    mock_response.json.return_value = {"id": "lg-1"}  # no field_data key

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await fetch_lead_fields("lg-1", "tok", settings=_settings())

    assert result == []


async def test_fetch_lead_fields_api_failure_raises_external_service_error() -> None:
    mock_response = MagicMock()
    mock_response.is_success = False
    mock_response.status_code = 400
    mock_response.text = "Invalid OAuth access token"

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(ExternalServiceError):
            await fetch_lead_fields("lg-bad", "bad-token", settings=_settings())
