"""Unit tests for oauth/facebook.py — no DB, no network."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest

from modules.lead_ingestion.exceptions import ChannelApiError, OAuthStateError
from modules.lead_ingestion.oauth.facebook import build_facebook_auth_url, exchange_facebook_code
from modules.lead_ingestion.oauth.state import sign_state, verify_state

_SECRET = "test-app-secret-0123456789abcdef"


def _settings() -> MagicMock:
    s = MagicMock()
    s.meta_app_id = "test-app-id"
    s.meta_app_secret = _SECRET
    s.meta_graph_api_version = "v21.0"
    s.base_url = "https://example.ngrok.io"
    s.channel_credentials_encryption_key = "dGVzdGtleV90ZXN0a2V5X3Rlc3RrZXlfdGVzdA=="
    return s


# ---------------------------------------------------------------------------
# build_facebook_auth_url — pure function, no mocking needed
# ---------------------------------------------------------------------------


def test_build_url_points_to_facebook_dialog() -> None:
    url = build_facebook_auth_url(uuid.uuid4(), settings=_settings())
    assert url.startswith("https://www.facebook.com/dialog/oauth")


def test_build_url_contains_all_required_scopes() -> None:
    url = build_facebook_auth_url(uuid.uuid4(), settings=_settings())
    for scope in ["pages_show_list", "leads_retrieval", "pages_messaging", "ads_management"]:
        assert scope in url


def test_build_url_callback_in_redirect_uri() -> None:
    url = build_facebook_auth_url(uuid.uuid4(), settings=_settings())
    # urlencode encodes slashes; check for either form
    assert "facebook%2Fcallback" in url or "facebook/callback" in url


def test_build_url_state_carries_tenant_id_and_channel() -> None:
    tenant_id = uuid.uuid4()
    url = build_facebook_auth_url(tenant_id, settings=_settings())
    qs = parse_qs(urlparse(url).query)
    state_token = qs["state"][0]
    payload = verify_state(state_token, secret=_SECRET)
    assert payload["tenant_id"] == str(tenant_id)
    assert payload["channel"] == "facebook"


# ---------------------------------------------------------------------------
# exchange_facebook_code — happy path
# ---------------------------------------------------------------------------


async def test_exchange_code_happy_path_creates_one_connection_per_page() -> None:
    s = _settings()
    tenant_id = uuid.uuid4()
    state = sign_state({"tenant_id": str(tenant_id), "channel": "facebook"}, secret=_SECRET)
    session = AsyncMock()
    session.add = MagicMock()

    with (
        patch(
            "modules.lead_ingestion.oauth.facebook._exchange_code_for_short_lived",
            new=AsyncMock(return_value="short-token"),
        ),
        patch(
            "modules.lead_ingestion.oauth.facebook._exchange_short_for_long_lived",
            new=AsyncMock(return_value="long-token"),
        ),
        patch(
            "modules.lead_ingestion.oauth.facebook._fetch_page_accounts",
            new=AsyncMock(
                return_value=[
                    {"id": "p1", "name": "Page One", "access_token": "tok-1"},
                    {"id": "p2", "name": "Page Two", "access_token": "tok-2"},
                ]
            ),
        ),
        patch(
            "modules.lead_ingestion.oauth.facebook._subscribe_page_webhooks",
            new=AsyncMock(),
        ),
        patch(
            "modules.lead_ingestion.oauth.facebook.encrypt_credentials",
            return_value=b"encrypted",
        ),
    ):
        connections = await exchange_facebook_code("auth-code", state, session=session, settings=s)

    assert len(connections) == 2
    assert connections[0].channel_type == "facebook"
    assert connections[0].connection_metadata == {"page_id": "p1", "page_name": "Page One"}
    assert connections[1].connection_metadata == {"page_id": "p2", "page_name": "Page Two"}
    assert session.add.call_count == 2
    session.commit.assert_called_once()


async def test_exchange_code_sets_tenant_id_on_connection() -> None:
    s = _settings()
    tenant_id = uuid.uuid4()
    state = sign_state({"tenant_id": str(tenant_id), "channel": "facebook"}, secret=_SECRET)
    session = AsyncMock()
    session.add = MagicMock()

    with (
        patch(
            "modules.lead_ingestion.oauth.facebook._exchange_code_for_short_lived",
            new=AsyncMock(return_value="short"),
        ),
        patch(
            "modules.lead_ingestion.oauth.facebook._exchange_short_for_long_lived",
            new=AsyncMock(return_value="long"),
        ),
        patch(
            "modules.lead_ingestion.oauth.facebook._fetch_page_accounts",
            new=AsyncMock(return_value=[{"id": "p1", "name": "P1", "access_token": "t1"}]),
        ),
        patch("modules.lead_ingestion.oauth.facebook._subscribe_page_webhooks", new=AsyncMock()),
        patch(
            "modules.lead_ingestion.oauth.facebook.encrypt_credentials", return_value=b"enc"
        ),
    ):
        connections = await exchange_facebook_code("code", state, session=session, settings=s)

    assert connections[0].tenant_id == tenant_id


# ---------------------------------------------------------------------------
# exchange_facebook_code — error paths
# ---------------------------------------------------------------------------


async def test_exchange_code_invalid_state_raises_oauth_state_error() -> None:
    session = AsyncMock()
    session.add = MagicMock()
    with pytest.raises(OAuthStateError):
        await exchange_facebook_code("code", "bad.state.token", session=session, settings=_settings())


async def test_exchange_code_api_failure_raises_channel_api_error() -> None:
    s = _settings()
    tenant_id = uuid.uuid4()
    state = sign_state({"tenant_id": str(tenant_id), "channel": "facebook"}, secret=_SECRET)
    session = AsyncMock()
    session.add = MagicMock()

    with patch(
        "modules.lead_ingestion.oauth.facebook._exchange_code_for_short_lived",
        new=AsyncMock(side_effect=ChannelApiError("Graph API down")),
    ):
        with pytest.raises(ChannelApiError):
            await exchange_facebook_code("code", state, session=session, settings=s)
