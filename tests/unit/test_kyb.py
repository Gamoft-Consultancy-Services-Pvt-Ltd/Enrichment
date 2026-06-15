"""Unit tests for KYB orchestration — service + Surepass client are mocked."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from core.exceptions import ConflictError
from modules.tenant_onboarding import kyb
from shared.tenant.schemas import KybStatus

_GSTIN = "29ABCDE1234F1Z5"


def _tenant(**over: object) -> SimpleNamespace:
    base: dict[str, object] = {
        "gstin": _GSTIN,
        "kyb_status": KybStatus.PENDING,
        "kyb_txn_ref": "txn-1",
        "kyb_attempts": 0,
        "kyb_resends": 0,
    }
    base.update(over)
    return SimpleNamespace(**base)


async def test_start_verification_sends_and_stores_txn() -> None:
    session = AsyncMock()
    tid = uuid4()
    with (
        patch.object(kyb.tenant_service, "get_tenant", AsyncMock(return_value=_tenant())),
        patch.object(kyb.surepass_client, "send_gst_otp", AsyncMock(return_value="txn-9")),
        patch.object(kyb.tenant_service, "store_kyb_txn", AsyncMock()) as store,
    ):
        await kyb.start_verification(session, tid)
    store.assert_awaited_once_with(session, tid, "txn-9")


async def test_submit_otp_verified() -> None:
    session = AsyncMock()
    tid = uuid4()
    company = {"gstin": _GSTIN, "legal_name": "ACME"}
    with (
        patch.object(kyb.tenant_service, "get_tenant", AsyncMock(return_value=_tenant())),
        patch.object(kyb.surepass_client, "verify_gst_otp", AsyncMock(return_value=company)),
        patch.object(kyb.tenant_service, "mark_kyb_verified", AsyncMock()) as verified,
    ):
        result = await kyb.submit_otp(session, tid, "123456")
    assert result == KybStatus.VERIFIED
    verified.assert_awaited_once_with(session, tid, company)


async def test_submit_otp_wrong_under_cap_stays_pending() -> None:
    session = AsyncMock()
    tid = uuid4()
    with (
        patch.object(kyb.tenant_service, "get_tenant", AsyncMock(return_value=_tenant())),
        patch.object(kyb.surepass_client, "verify_gst_otp", AsyncMock(return_value=None)),
        patch.object(kyb.tenant_service, "bump_kyb_attempts", AsyncMock(return_value=1)),
        patch.object(kyb.tenant_service, "mark_kyb_failed", AsyncMock()) as failed,
    ):
        result = await kyb.submit_otp(session, tid, "000000")
    assert result == KybStatus.PENDING
    failed.assert_not_awaited()


async def test_submit_otp_wrong_at_cap_fails() -> None:
    session = AsyncMock()
    tid = uuid4()
    with (
        patch.object(kyb.tenant_service, "get_tenant", AsyncMock(return_value=_tenant())),
        patch.object(kyb.surepass_client, "verify_gst_otp", AsyncMock(return_value=None)),
        patch.object(kyb.tenant_service, "bump_kyb_attempts", AsyncMock(return_value=3)),
        patch.object(kyb.tenant_service, "mark_kyb_failed", AsyncMock()) as failed,
    ):
        result = await kyb.submit_otp(session, tid, "000000")
    assert result == KybStatus.FAILED
    failed.assert_awaited_once_with(session, tid)


async def test_submit_otp_not_pending_conflicts() -> None:
    session = AsyncMock()
    with patch.object(
        kyb.tenant_service, "get_tenant",
        AsyncMock(return_value=_tenant(kyb_status=KybStatus.VERIFIED)),
    ):
        with pytest.raises(ConflictError):
            await kyb.submit_otp(session, uuid4(), "123456")


async def test_resend_over_cap_conflicts() -> None:
    session = AsyncMock()
    with patch.object(
        kyb.tenant_service, "get_tenant",
        AsyncMock(return_value=_tenant(kyb_resends=3)),
    ):
        with pytest.raises(ConflictError):
            await kyb.resend_otp(session, uuid4())


async def test_restart_requires_failed_status() -> None:
    session = AsyncMock()
    with patch.object(
        kyb.tenant_service, "get_tenant",
        AsyncMock(return_value=_tenant(kyb_status=KybStatus.PENDING)),
    ):
        with pytest.raises(ConflictError):
            await kyb.restart_kyb(session, uuid4(), None)
