"""Unit tests for modules.tenant_onboarding.kyb."""

from typing import Any
from unittest.mock import AsyncMock

import pytest

from modules.tenant_onboarding.kyb import verify_pan_kyb


def _check(**overrides: Any) -> dict[str, Any]:
    base = {"category": "company", "status": "valid", "name_match": True, "dob_match": True}
    return {**base, **overrides}


async def test_verify_pan_kyb_verified(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("clients.pan_client.verify_pan", AsyncMock(return_value=_check()))
    result = await verify_pan_kyb("AAACX1234C", "Gamoft", "01/04/2019")
    assert result == {"name": "Gamoft", "category": "company", "status": "valid"}


async def test_verify_pan_kyb_rejects_name_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "clients.pan_client.verify_pan", AsyncMock(return_value=_check(name_match=False))
    )
    assert await verify_pan_kyb("AAACX1234C", "Wrong", "01/04/2019") is None


async def test_verify_pan_kyb_rejects_dob_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "clients.pan_client.verify_pan", AsyncMock(return_value=_check(dob_match=False))
    )
    assert await verify_pan_kyb("AAACX1234C", "Gamoft", "02/02/2002") is None


async def test_verify_pan_kyb_rejects_invalid_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "clients.pan_client.verify_pan",
        AsyncMock(return_value=_check(status="invalid", category="")),
    )
    assert await verify_pan_kyb("AAAAA0000A", "X", "01/01/2000") is None
