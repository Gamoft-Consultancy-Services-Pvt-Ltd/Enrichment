"""Unit tests for oauth/instagram.py — no DB, no network."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest

from modules.lead_ingestion.exceptions import ChannelApiError, OAuthStateError
from modules.lead_ingestion.oauth.instagram import (
    _subscribe_ig_account,
    build_instagram_auth_url,
    exchange_instagram_code,
)
from modules.lead_ingestion.oauth.state import sign_state, verify_state

_SECRET = "test-instagram-secret-0123456789ab"


def _settings() -> MagicMock:
    s = MagicMock()
    s.meta_app_id = "test-app-id"
    s.meta_app_secret = _SECRET
    s.meta_graph_api_version = "v21.0"
    s.base_url = "https://example.ngrok.io"
    s.channel_credentials_encryption_key = "dGVzdGtleV90ZXN0a2V5X3Rlc3RrZXlfdGVzdA=="
    return s


# ---------------------------------------------------------------------------
# build_instagram_auth_url — pure function
# ---------------------------------------------------------------------------


def test_build_url_points_to_instagram_authorize() -> None:
    url = build_instagram_auth_url(uuid.uuid4(), settings=_settings())
    assert url.startswith("https://api.instagram.com/oauth/authorize")


def test_build_url_contains_instagram_business_scopes() -> None:
    url = build_instagram_auth_url(uuid.uuid4(), settings=_settings())
    assert "instagram_business_basic" in url
    assert "instagram_business_manage_messages" in url


def test_build_url_callback_in_redirect_uri() -> None:
    url = build_instagram_auth_url(uuid.uuid4(), settings=_settings())
    assert "instagram%2Fcallback" in url or "instagram/callback" in url


def test_build_url_state_carries_tenant_id_and_channel() -> None:
    tenant_id = uuid.uuid4()
    url = build_instagram_auth_url(tenant_id, settings=_settings())
    qs = parse_qs(urlparse(url).query)
    state_token = qs["state"][0]
    payload = verify_state(state_token, secret=_SECRET)
    assert payload["tenant_id"] == str(tenant_id)
    assert payload["channel"] == "instagram"


# ---------------------------------------------------------------------------
# exchange_instagram_code — happy path
# ---------------------------------------------------------------------------


async def test_exchange_instagram_code_creates_connection() -> None:
    s = _settings()
    tenant_id = uuid.uuid4()
    state = sign_state({"tenant_id": str(tenant_id), "channel": "instagram"}, secret=_SECRET)
    session = AsyncMock()
    session.add = MagicMock()

    with (
        patch(
            "modules.lead_ingestion.oauth.instagram._exchange_code_for_short_lived",
            new=AsyncMock(return_value="short-token"),
        ),
        patch(
            "modules.lead_ingestion.oauth.instagram._exchange_short_for_long_lived",
            new=AsyncMock(return_value=("long-token", 5183944)),
        ),
        patch(
            "modules.lead_ingestion.oauth.instagram._fetch_ig_account_id",
            new=AsyncMock(return_value=("ig-123456", "mytestaccount")),
        ),
        patch(
            "modules.lead_ingestion.oauth.instagram._subscribe_ig_account",
            new=AsyncMock(),
        ),
        patch(
            "modules.lead_ingestion.oauth.instagram.encrypt_credentials",
            return_value=b"encrypted",
        ),
    ):
        conn = await exchange_instagram_code("auth-code", state, session=session, settings=s)

    assert conn.channel_type == "instagram"
    assert conn.connection_metadata is not None
    assert conn.connection_metadata["ig_account_id"] == "ig-123456"
    assert conn.connection_metadata["username"] == "mytestaccount"
    assert conn.expires_at is not None
    session.add.assert_called_once_with(conn)
    session.commit.assert_called_once()


async def test_exchange_instagram_code_sets_tenant_id() -> None:
    s = _settings()
    tenant_id = uuid.uuid4()
    state = sign_state({"tenant_id": str(tenant_id), "channel": "instagram"}, secret=_SECRET)
    session = AsyncMock()
    session.add = MagicMock()

    with (
        patch(
            "modules.lead_ingestion.oauth.instagram._exchange_code_for_short_lived",
            new=AsyncMock(return_value="short"),
        ),
        patch(
            "modules.lead_ingestion.oauth.instagram._exchange_short_for_long_lived",
            new=AsyncMock(return_value=("long", 5183944)),
        ),
        patch(
            "modules.lead_ingestion.oauth.instagram._fetch_ig_account_id",
            new=AsyncMock(return_value=("ig-abc", "user")),
        ),
        patch(
            "modules.lead_ingestion.oauth.instagram._subscribe_ig_account",
            new=AsyncMock(),
        ),
        patch(
            "modules.lead_ingestion.oauth.instagram.encrypt_credentials",
            return_value=b"enc",
        ),
    ):
        conn = await exchange_instagram_code("code", state, session=session, settings=s)

    assert conn.tenant_id == tenant_id


# ---------------------------------------------------------------------------
# exchange_instagram_code — error paths
# ---------------------------------------------------------------------------


async def test_exchange_instagram_code_invalid_state_raises() -> None:
    session = AsyncMock()
    session.add = MagicMock()
    with pytest.raises(OAuthStateError):
        await exchange_instagram_code("code", "bad.state", session=session, settings=_settings())


async def test_exchange_instagram_code_api_failure_raises_channel_api_error() -> None:
    s = _settings()
    tenant_id = uuid.uuid4()
    state = sign_state({"tenant_id": str(tenant_id), "channel": "instagram"}, secret=_SECRET)
    session = AsyncMock()
    session.add = MagicMock()

    with patch(
        "modules.lead_ingestion.oauth.instagram._exchange_code_for_short_lived",
        new=AsyncMock(side_effect=ChannelApiError("Instagram API down")),
    ):
        with pytest.raises(ChannelApiError):
            await exchange_instagram_code("code", state, session=session, settings=s)


# ---------------------------------------------------------------------------
# _subscribe_ig_account — subscription webhook call
# ---------------------------------------------------------------------------


async def test_subscribe_ig_account_calls_subscribed_apps_endpoint() -> None:
    s = _settings()
    mock_response = MagicMock()
    mock_response.is_success = True

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        await _subscribe_ig_account("ig-123", "long-token", settings=s)

    mock_client.post.assert_called_once()
    call_args = mock_client.post.call_args
    assert "ig-123/subscribed_apps" in call_args[0][0]
    assert call_args[1]["params"]["subscribed_fields"] == "messages"


async def test_subscribe_ig_account_api_failure_raises_channel_api_error() -> None:
    s = _settings()
    mock_response = MagicMock()
    mock_response.is_success = False
    mock_response.status_code = 400
    mock_response.text = "bad request"

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(ChannelApiError):
            await _subscribe_ig_account("ig-123", "bad-token", settings=s)


async def test_exchange_instagram_code_calls_subscribe() -> None:
    s = _settings()
    tenant_id = uuid.uuid4()
    state = sign_state({"tenant_id": str(tenant_id), "channel": "instagram"}, secret=_SECRET)
    session = AsyncMock()
    session.add = MagicMock()
    subscribe_mock = AsyncMock()

    with (
        patch(
            "modules.lead_ingestion.oauth.instagram._exchange_code_for_short_lived",
            new=AsyncMock(return_value="short"),
        ),
        patch(
            "modules.lead_ingestion.oauth.instagram._exchange_short_for_long_lived",
            new=AsyncMock(return_value=("long-t", 5183944)),
        ),
        patch(
            "modules.lead_ingestion.oauth.instagram._fetch_ig_account_id",
            new=AsyncMock(return_value=("ig-99", "acct")),
        ),
        patch(
            "modules.lead_ingestion.oauth.instagram._subscribe_ig_account",
            new=subscribe_mock,
        ),
        patch(
            "modules.lead_ingestion.oauth.instagram.encrypt_credentials",
            return_value=b"e",
        ),
    ):
        await exchange_instagram_code("code", state, session=session, settings=s)

    subscribe_mock.assert_called_once_with("ig-99", "long-t", settings=s)
