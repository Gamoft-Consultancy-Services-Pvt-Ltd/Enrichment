"""Unit tests for oauth/facebook.py — no DB, no network."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest

from modules.lead_ingestion.exceptions import ChannelApiError, OAuthStateError
from modules.lead_ingestion.oauth.facebook import (
    _fetch_connected_ig_account,
    _subscribe_page_webhooks,
    build_facebook_auth_url,
    exchange_facebook_code,
)
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
    for scope in [
        "pages_show_list",
        "pages_messaging",
        "pages_manage_metadata",
        "pages_read_engagement",
    ]:
        assert scope in url


def test_build_url_contains_instagram_scopes() -> None:
    url = build_facebook_auth_url(uuid.uuid4(), settings=_settings())
    assert "instagram_manage_messages" in url
    assert "instagram_basic" in url


def test_build_url_contains_lead_ads_scopes() -> None:
    url = build_facebook_auth_url(uuid.uuid4(), settings=_settings())
    assert "leads_retrieval" in url
    assert "pages_manage_ads" in url


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
# _fetch_connected_ig_account
# ---------------------------------------------------------------------------


async def test_fetch_connected_ig_account_returns_id_when_present() -> None:
    s = _settings()
    mock_response = MagicMock()
    mock_response.is_success = True
    mock_response.json.return_value = {
        "id": "page-1",
        "instagram_business_account": {"id": "ig-789"},
    }
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await _fetch_connected_ig_account("page-1", "page-token", settings=s)

    assert result == "ig-789"
    call_params = mock_client.get.call_args[1]["params"]
    assert "instagram_business_account" in call_params["fields"]


async def test_fetch_connected_ig_account_returns_none_when_field_absent() -> None:
    s = _settings()
    mock_response = MagicMock()
    mock_response.is_success = True
    mock_response.json.return_value = {"id": "page-1"}  # no instagram_business_account key

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await _fetch_connected_ig_account("page-1", "page-token", settings=s)

    assert result is None


async def test_fetch_connected_ig_account_returns_none_on_api_failure() -> None:
    s = _settings()
    mock_response = MagicMock()
    mock_response.is_success = False

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await _fetch_connected_ig_account("page-1", "bad-token", settings=s)

    assert result is None


# ---------------------------------------------------------------------------
# exchange_facebook_code — happy path (no Instagram linked)
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
            "modules.lead_ingestion.oauth.facebook._fetch_connected_ig_account",
            new=AsyncMock(return_value=None),  # no Instagram on either page
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
            "modules.lead_ingestion.oauth.facebook._fetch_connected_ig_account",
            new=AsyncMock(return_value=None),
        ),
        patch("modules.lead_ingestion.oauth.facebook.encrypt_credentials", return_value=b"enc"),
    ):
        connections = await exchange_facebook_code("code", state, session=session, settings=s)

    assert connections[0].tenant_id == tenant_id


# ---------------------------------------------------------------------------
# exchange_facebook_code — Instagram connection via page token
# ---------------------------------------------------------------------------


async def test_exchange_code_creates_instagram_connection_when_ig_linked() -> None:
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
            new=AsyncMock(return_value=[{"id": "p1", "name": "Page One", "access_token": "tok-1"}]),
        ),
        patch("modules.lead_ingestion.oauth.facebook._subscribe_page_webhooks", new=AsyncMock()),
        patch(
            "modules.lead_ingestion.oauth.facebook._fetch_connected_ig_account",
            new=AsyncMock(return_value="ig-999"),
        ),
        patch(
            "modules.lead_ingestion.oauth.facebook._subscribe_ig_via_page_token",
            new=AsyncMock(),
        ),
        patch("modules.lead_ingestion.oauth.facebook.encrypt_credentials", return_value=b"enc"),
    ):
        connections = await exchange_facebook_code("code", state, session=session, settings=s)

    assert len(connections) == 2
    fb_conn = next(c for c in connections if c.channel_type == "facebook")
    ig_conn = next(c for c in connections if c.channel_type == "instagram")
    assert fb_conn.connection_metadata == {"page_id": "p1", "page_name": "Page One"}
    assert ig_conn.connection_metadata == {"ig_account_id": "ig-999"}
    assert ig_conn.tenant_id == tenant_id


async def test_exchange_code_instagram_connection_has_no_expiry() -> None:
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
            new=AsyncMock(return_value=[{"id": "p1", "name": "P1", "access_token": "tok-1"}]),
        ),
        patch("modules.lead_ingestion.oauth.facebook._subscribe_page_webhooks", new=AsyncMock()),
        patch(
            "modules.lead_ingestion.oauth.facebook._fetch_connected_ig_account",
            new=AsyncMock(return_value="ig-999"),
        ),
        patch(
            "modules.lead_ingestion.oauth.facebook._subscribe_ig_via_page_token",
            new=AsyncMock(),
        ),
        patch("modules.lead_ingestion.oauth.facebook.encrypt_credentials", return_value=b"enc"),
    ):
        connections = await exchange_facebook_code("code", state, session=session, settings=s)

    ig_conn = next(c for c in connections if c.channel_type == "instagram")
    assert ig_conn.expires_at is None


async def test_exchange_code_skips_instagram_when_not_linked() -> None:
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
            new=AsyncMock(return_value=[{"id": "p1", "name": "P1", "access_token": "tok-1"}]),
        ),
        patch("modules.lead_ingestion.oauth.facebook._subscribe_page_webhooks", new=AsyncMock()),
        patch(
            "modules.lead_ingestion.oauth.facebook._fetch_connected_ig_account",
            new=AsyncMock(return_value=None),
        ),
        patch("modules.lead_ingestion.oauth.facebook.encrypt_credentials", return_value=b"enc"),
    ):
        connections = await exchange_facebook_code("code", state, session=session, settings=s)

    assert len(connections) == 1
    assert connections[0].channel_type == "facebook"


async def test_exchange_code_calls_ig_subscribe_when_ig_linked() -> None:
    s = _settings()
    tenant_id = uuid.uuid4()
    state = sign_state({"tenant_id": str(tenant_id), "channel": "facebook"}, secret=_SECRET)
    session = AsyncMock()
    session.add = MagicMock()
    ig_subscribe_mock = AsyncMock()

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
            new=AsyncMock(return_value=[{"id": "p1", "name": "P1", "access_token": "page-tok"}]),
        ),
        patch("modules.lead_ingestion.oauth.facebook._subscribe_page_webhooks", new=AsyncMock()),
        patch(
            "modules.lead_ingestion.oauth.facebook._fetch_connected_ig_account",
            new=AsyncMock(return_value="ig-999"),
        ),
        patch(
            "modules.lead_ingestion.oauth.facebook._subscribe_ig_via_page_token",
            new=ig_subscribe_mock,
        ),
        patch("modules.lead_ingestion.oauth.facebook.encrypt_credentials", return_value=b"enc"),
    ):
        await exchange_facebook_code("code", state, session=session, settings=s)

    ig_subscribe_mock.assert_called_once_with("ig-999", "page-tok", settings=s)


# ---------------------------------------------------------------------------
# exchange_facebook_code — error paths
# ---------------------------------------------------------------------------


async def test_exchange_code_invalid_state_raises_oauth_state_error() -> None:
    session = AsyncMock()
    session.add = MagicMock()
    with pytest.raises(OAuthStateError):
        await exchange_facebook_code(
            "code", "bad.state.token", session=session, settings=_settings()
        )


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


# ---------------------------------------------------------------------------
# _subscribe_page_webhooks — leadgen field included
# ---------------------------------------------------------------------------


async def test_subscribe_page_includes_leadgen_in_subscribed_fields() -> None:
    s = _settings()
    mock_response = MagicMock()
    mock_response.is_success = True

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        await _subscribe_page_webhooks("page-1", "page-tok", settings=s)

    params = mock_client.post.call_args[1]["params"]
    subscribed = params["subscribed_fields"]
    assert "messages" in subscribed
    assert "leadgen" in subscribed
