"""Unit tests for oauth/whatsapp.py — no DB, no network."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from modules.lead_ingestion.exceptions import ChannelApiError
from modules.lead_ingestion.oauth.whatsapp import exchange_whatsapp_signup_code

_SECRET = "test-whatsapp-secret-0123456789ab"


def _settings() -> MagicMock:
    s = MagicMock()
    s.meta_app_id = "test-app-id"
    s.meta_app_secret = _SECRET
    s.meta_graph_api_version = "v21.0"
    s.base_url = "https://example.ngrok.io"
    s.channel_credentials_encryption_key = "dGVzdGtleV90ZXN0a2V5X3Rlc3RrZXlfdGVzdA=="
    return s


async def test_exchange_signup_code_creates_one_connection_per_phone_number() -> None:
    s = _settings()
    tenant_id = uuid.uuid4()
    session = AsyncMock()
    session.add = MagicMock()

    with (
        patch(
            "modules.lead_ingestion.oauth.whatsapp._exchange_signup_code",
            new=AsyncMock(return_value="bisu-token"),
        ),
        patch(
            "modules.lead_ingestion.oauth.whatsapp._get_waba_ids",
            new=AsyncMock(return_value=["waba-111"]),
        ),
        patch(
            "modules.lead_ingestion.oauth.whatsapp._get_phone_numbers",
            new=AsyncMock(
                return_value=[
                    {"id": "phone-1", "display_phone_number": "+91 98765 43210"},
                    {"id": "phone-2", "display_phone_number": "+91 11111 22222"},
                ]
            ),
        ),
        patch(
            "modules.lead_ingestion.oauth.whatsapp._subscribe_waba",
            new=AsyncMock(),
        ),
        patch(
            "modules.lead_ingestion.oauth.whatsapp.encrypt_credentials",
            return_value=b"encrypted",
        ),
    ):
        connections = await exchange_whatsapp_signup_code(
            "signup-code", session=session, settings=s, tenant_id=tenant_id
        )

    assert len(connections) == 2
    assert connections[0].channel_type == "whatsapp"
    assert connections[0].connection_metadata == {
        "phone_number_id": "phone-1",
        "display_phone_number": "+91 98765 43210",
        "waba_id": "waba-111",
    }
    assert connections[1].connection_metadata == {
        "phone_number_id": "phone-2",
        "display_phone_number": "+91 11111 22222",
        "waba_id": "waba-111",
    }
    assert session.add.call_count == 2
    session.commit.assert_called_once()


async def test_exchange_signup_code_multiple_wabas() -> None:
    s = _settings()
    tenant_id = uuid.uuid4()
    session = AsyncMock()
    session.add = MagicMock()

    with (
        patch(
            "modules.lead_ingestion.oauth.whatsapp._exchange_signup_code",
            new=AsyncMock(return_value="bisu-token"),
        ),
        patch(
            "modules.lead_ingestion.oauth.whatsapp._get_waba_ids",
            new=AsyncMock(return_value=["waba-A", "waba-B"]),
        ),
        patch(
            "modules.lead_ingestion.oauth.whatsapp._get_phone_numbers",
            new=AsyncMock(return_value=[{"id": "phone-X", "display_phone_number": "+1 555 0100"}]),
        ),
        patch("modules.lead_ingestion.oauth.whatsapp._subscribe_waba", new=AsyncMock()),
        patch("modules.lead_ingestion.oauth.whatsapp.encrypt_credentials", return_value=b"enc"),
    ):
        connections = await exchange_whatsapp_signup_code(
            "code", session=session, settings=s, tenant_id=tenant_id
        )

    # One phone per WABA × 2 WABAs = 2 connections
    assert len(connections) == 2
    assert session.add.call_count == 2


async def test_exchange_signup_code_sets_tenant_id() -> None:
    s = _settings()
    tenant_id = uuid.uuid4()
    session = AsyncMock()
    session.add = MagicMock()

    with (
        patch(
            "modules.lead_ingestion.oauth.whatsapp._exchange_signup_code",
            new=AsyncMock(return_value="token"),
        ),
        patch(
            "modules.lead_ingestion.oauth.whatsapp._get_waba_ids",
            new=AsyncMock(return_value=["waba-1"]),
        ),
        patch(
            "modules.lead_ingestion.oauth.whatsapp._get_phone_numbers",
            new=AsyncMock(return_value=[{"id": "ph-1", "display_phone_number": "+1"}]),
        ),
        patch("modules.lead_ingestion.oauth.whatsapp._subscribe_waba", new=AsyncMock()),
        patch("modules.lead_ingestion.oauth.whatsapp.encrypt_credentials", return_value=b"e"),
    ):
        connections = await exchange_whatsapp_signup_code(
            "code", session=session, settings=s, tenant_id=tenant_id
        )

    assert connections[0].tenant_id == tenant_id


async def test_exchange_signup_code_api_failure_raises_channel_api_error() -> None:
    s = _settings()
    session = AsyncMock()
    session.add = MagicMock()
    with patch(
        "modules.lead_ingestion.oauth.whatsapp._exchange_signup_code",
        new=AsyncMock(side_effect=ChannelApiError("token exchange failed")),
    ):
        with pytest.raises(ChannelApiError):
            await exchange_whatsapp_signup_code(
                "bad-code", session=session, settings=s, tenant_id=uuid.uuid4()
            )


async def test_exchange_signup_code_no_phones_returns_empty_list() -> None:
    s = _settings()
    tenant_id = uuid.uuid4()
    session = AsyncMock()
    session.add = MagicMock()

    with (
        patch(
            "modules.lead_ingestion.oauth.whatsapp._exchange_signup_code",
            new=AsyncMock(return_value="token"),
        ),
        patch(
            "modules.lead_ingestion.oauth.whatsapp._get_waba_ids",
            new=AsyncMock(return_value=["waba-1"]),
        ),
        patch(
            "modules.lead_ingestion.oauth.whatsapp._get_phone_numbers",
            new=AsyncMock(return_value=[]),
        ),
        patch("modules.lead_ingestion.oauth.whatsapp._subscribe_waba", new=AsyncMock()),
        patch("modules.lead_ingestion.oauth.whatsapp.encrypt_credentials", return_value=b"e"),
    ):
        connections = await exchange_whatsapp_signup_code(
            "code", session=session, settings=s, tenant_id=tenant_id
        )

    assert connections == []
    session.add.assert_not_called()
