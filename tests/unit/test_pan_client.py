"""Unit tests for clients.pan_client — mock path + live-path failure handling.

The mock-path tests force pan_use_mock=True. The live-path tests force it False
and stub the HTTP with respx (no real network), asserting that the happy path
parses and that every failure mode raises ExternalServiceError with the GENERIC
message (provider detail is logged, never surfaced to the caller).
"""

from typing import Any

import httpx
import pytest
import respx

from clients.pan_client import _GENERIC_ERROR, verify_pan
from core.config import Settings
from core.exceptions import ExternalServiceError
from tests.helpers import build_settings

_BASE = "https://test-api.sandbox.co.in"


def _settings(**overrides: Any) -> Settings:
    defaults: dict[str, Any] = {
        "pan_use_mock": True,
        "pan_api_key": "k",
        "pan_api_secret": "s",
        "pan_base_url": _BASE,
    }
    return build_settings(**{**defaults, **overrides})


@pytest.fixture
def _force_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("clients.pan_client.get_settings", lambda: _settings())


@pytest.fixture
def _force_live(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("clients.pan_client.get_settings", lambda: _settings(pan_use_mock=False))


@pytest.mark.usefixtures("_force_mock")
async def test_verify_pan_valid_company_pan() -> None:
    result = await verify_pan("AAACX1234C", "Gamoft Consultancy Pvt Ltd", "01/04/2019")
    assert result["status"] == "valid"
    assert result["category"] == "company"  # 4th char C
    assert result["name_match"] is True
    assert result["dob_match"] is True


@pytest.mark.usefixtures("_force_mock")
async def test_verify_pan_sentinel_is_not_valid() -> None:
    result = await verify_pan("AAAAA0000A", "Whoever", "01/01/2000")
    assert result["status"] != "valid"
    assert result["name_match"] is False


@pytest.mark.usefixtures("_force_mock")
async def test_verify_pan_too_short_returns_invalid() -> None:
    # A PAN shorter than 4 chars must not raise IndexError — treated as invalid.
    result = await verify_pan("ABC", "Whoever", "01/01/2000")
    assert result["status"] != "valid"
    assert result["name_match"] is False


@pytest.mark.usefixtures("_force_live")
@respx.mock
async def test_verify_pan_live_parses_data() -> None:
    respx.post(f"{_BASE}/authenticate").mock(
        return_value=httpx.Response(200, json={"access_token": "jwt"})
    )
    respx.post(f"{_BASE}/kyc/pan/verify").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "category": "company",
                    "status": "valid",
                    "name_as_per_pan_match": True,
                    "date_of_birth_match": True,
                }
            },
        )
    )
    result = await verify_pan("AAACX1234C", "Gamoft", "01/04/2019")
    assert result == {
        "category": "company",
        "status": "valid",
        "name_match": True,
        "dob_match": True,
    }


@pytest.mark.usefixtures("_force_live")
@respx.mock
async def test_verify_pan_live_authenticate_non_200_raises_generic() -> None:
    # /authenticate returns 401 -> ExternalServiceError with generic message, no detail leaked.
    respx.post(f"{_BASE}/authenticate").mock(return_value=httpx.Response(401))
    with pytest.raises(ExternalServiceError) as exc_info:
        await verify_pan("AAACX1234C", "Gamoft", "01/04/2019")
    assert str(exc_info.value) == _GENERIC_ERROR


@pytest.mark.usefixtures("_force_live")
@respx.mock
async def test_verify_pan_live_non_200_raises_generic() -> None:
    respx.post(f"{_BASE}/authenticate").mock(
        return_value=httpx.Response(200, json={"access_token": "jwt"})
    )
    respx.post(f"{_BASE}/kyc/pan/verify").mock(return_value=httpx.Response(503))
    with pytest.raises(ExternalServiceError) as exc_info:
        await verify_pan("AAACX1234C", "Gamoft", "01/04/2019")
    assert str(exc_info.value) == _GENERIC_ERROR  # no provider detail leaked


@pytest.mark.usefixtures("_force_live")
@respx.mock
async def test_verify_pan_live_unexpected_body_raises_generic() -> None:
    respx.post(f"{_BASE}/authenticate").mock(
        return_value=httpx.Response(200, json={"access_token": "jwt"})
    )
    # 200 but the 'data' envelope is missing -> defensive parse -> generic 502, not 500.
    respx.post(f"{_BASE}/kyc/pan/verify").mock(
        return_value=httpx.Response(200, json={"unexpected": "shape"})
    )
    with pytest.raises(ExternalServiceError) as exc_info:
        await verify_pan("AAACX1234C", "Gamoft", "01/04/2019")
    assert str(exc_info.value) == _GENERIC_ERROR
