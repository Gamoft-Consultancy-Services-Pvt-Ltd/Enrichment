"""Unit tests for clients/surepass_client — mock path + live path (mocked httpx)."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clients.surepass_client import send_gst_otp, verify_gst_otp
from core.exceptions import ExternalServiceError
from tests.helpers import build_settings

_GSTIN = "29ABCDE1234F1Z5"


def _patch_settings(**overrides: Any) -> Any:
    return patch(
        "clients.surepass_client.get_settings",
        return_value=build_settings(**overrides),
    )


# --- mock path (no token) ---


async def test_mock_send_returns_txn_ref() -> None:
    with _patch_settings(surepass_use_mock=True):
        txn = await send_gst_otp(_GSTIN)
    assert isinstance(txn, str)
    assert txn != ""


async def test_mock_verify_accepts_dev_otp() -> None:
    with _patch_settings(surepass_use_mock=True):
        company = await verify_gst_otp("mock-txn", "123456")
    assert company is not None
    # In mock mode the gstin is derived from the txn_ref; just verify it's a non-empty str
    # and the record looks like an active company.
    assert isinstance(company["gstin"], str) and company["gstin"] != ""
    assert company["status"] == "Active"


async def test_mock_verify_rejects_wrong_otp() -> None:
    with _patch_settings(surepass_use_mock=True):
        company = await verify_gst_otp("mock-txn", "000000")
    assert company is None


# --- live path (mocked httpx) ---


def _resp(status: int, body: dict[str, Any]) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body
    return r


async def test_live_send_posts_and_returns_client_id() -> None:
    body = {"data": {"client_id": "abc-123"}}
    with _patch_settings(surepass_use_mock=False, surepass_api_key="k"):
        with patch("clients.surepass_client.httpx.AsyncClient") as mock_cls:
            mock_post = AsyncMock(return_value=_resp(200, body))
            mock_cls.return_value.__aenter__.return_value.post = mock_post
            txn = await send_gst_otp(_GSTIN)
    assert txn == "abc-123"


async def test_live_verify_returns_company_on_success() -> None:
    body = {
        "data": {
            "gstin": _GSTIN,
            "legal_name": "ACME PRIVATE LIMITED",
            "trade_name": "Acme",
            "status": "Active",
            "address": "1 Road, City",
        }
    }
    with _patch_settings(surepass_use_mock=False, surepass_api_key="k"):
        with patch("clients.surepass_client.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=_resp(200, body)
            )
            company = await verify_gst_otp("abc-123", "111111")
    assert company is not None
    assert company["legal_name"] == "ACME PRIVATE LIMITED"


async def test_live_verify_returns_none_on_otp_rejection() -> None:
    with _patch_settings(surepass_use_mock=False, surepass_api_key="k"):
        with patch("clients.surepass_client.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=_resp(422, {"message": "invalid otp"})
            )
            company = await verify_gst_otp("abc-123", "000000")
    assert company is None


async def test_live_send_raises_on_network_failure() -> None:
    with _patch_settings(surepass_use_mock=False, surepass_api_key="k"):
        with patch("clients.surepass_client.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__.return_value.post = AsyncMock(
                side_effect=Exception("connection refused")
            )
            with pytest.raises(ExternalServiceError, match="Surepass"):
                await send_gst_otp(_GSTIN)
