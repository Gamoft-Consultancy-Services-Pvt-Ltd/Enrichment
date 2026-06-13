"""Instagram OAuth for Business account connections (DMs).

2-step token chain on callback:
  code → short-lived token (via api.instagram.com, POST form data)
       → 60-day long-lived token (via graph.instagram.com)

Then fetch ig_account_id via graph.instagram.com/me, and subscribe the account
to this app's webhooks (required for Instagram Business Login — the generic
App Dashboard Webhooks section does not cover per-account subscriptions).

Public surface:
  build_instagram_auth_url(tenant_id, *, settings) -> str
  exchange_instagram_code(code, state, *, session, settings) -> ChannelConnection

Internal helpers (patchable in tests):
  _exchange_code_for_short_lived(code, redirect_uri, *, settings) -> str
  _exchange_short_for_long_lived(short_lived, *, settings) -> tuple[str, int]
  _fetch_ig_account_id(long_lived, *, settings) -> tuple[str, str]
  _subscribe_ig_account(ig_account_id, access_token, *, settings) -> None
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from sqlalchemy.ext.asyncio import AsyncSession

from core.config import Settings
from modules.lead_ingestion.crypto import encrypt_credentials
from modules.lead_ingestion.exceptions import ChannelApiError, OAuthStateError
from modules.lead_ingestion.oauth.state import sign_state, verify_state
from shared.channels.models import ChannelConnection

_AUTH_BASE = "https://api.instagram.com/oauth/authorize"
_TOKEN_BASE = "https://api.instagram.com/oauth/access_token"
_GRAPH_BASE = "https://graph.instagram.com"

_SCOPES = ",".join(
    [
        "instagram_business_basic",
        "instagram_business_manage_messages",
    ]
)


def build_instagram_auth_url(tenant_id: uuid.UUID, *, settings: Settings) -> str:
    """Build the Instagram OAuth redirect URL with an HMAC-signed state parameter."""
    state = sign_state(
        {"tenant_id": str(tenant_id), "channel": "instagram"},
        secret=settings.meta_app_secret,
    )
    redirect_uri = f"{settings.base_url}/channels/oauth/instagram/callback"
    params = {
        "client_id": settings.meta_app_id,
        "redirect_uri": redirect_uri,
        "scope": _SCOPES,
        "response_type": "code",
        "state": state,
    }
    return f"{_AUTH_BASE}?{urlencode(params)}"


async def _exchange_code_for_short_lived(
    code: str,
    redirect_uri: str,
    *,
    settings: Settings,
) -> str:
    """Exchange auth code for a short-lived Instagram token via POST form data."""
    import httpx

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _TOKEN_BASE,
            data={
                "client_id": settings.meta_app_id,
                "client_secret": settings.meta_app_secret,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
                "code": code,
            },
        )
    if not resp.is_success:
        raise ChannelApiError(
            f"Instagram code exchange failed: {resp.status_code} {resp.text}"
        )
    data: dict[str, Any] = resp.json()
    return str(data["access_token"])


async def _exchange_short_for_long_lived(
    short_lived: str,
    *,
    settings: Settings,
) -> tuple[str, int]:
    """Exchange a short-lived token for a 60-day long-lived token.

    Returns (access_token, expires_in_seconds).
    """
    import httpx

    version = settings.meta_graph_api_version
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{_GRAPH_BASE}/{version}/oauth/access_token",
            params={
                "grant_type": "ig_exchange_token",
                "client_id": settings.meta_app_id,
                "client_secret": settings.meta_app_secret,
                "access_token": short_lived,
            },
        )
    if not resp.is_success:
        raise ChannelApiError(
            f"Instagram long-lived token exchange failed: {resp.status_code} {resp.text}"
        )
    data: dict[str, Any] = resp.json()
    expires_in: int = int(data.get("expires_in", 5183944))
    return str(data["access_token"]), expires_in


async def _fetch_ig_account_id(
    long_lived_token: str,
    *,
    settings: Settings,
) -> tuple[str, str]:
    """Fetch the Instagram account's numeric ID and username.

    Returns (ig_account_id, username).
    """
    import httpx

    version = settings.meta_graph_api_version
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{_GRAPH_BASE}/{version}/me",
            params={"fields": "id,username", "access_token": long_lived_token},
        )
    if not resp.is_success:
        raise ChannelApiError(
            f"Failed to fetch Instagram account ID: {resp.status_code} {resp.text}"
        )
    data: dict[str, Any] = resp.json()
    return str(data["id"]), str(data.get("username", ""))


async def _subscribe_ig_account(
    ig_account_id: str,
    access_token: str,
    *,
    settings: Settings,
) -> None:
    """Subscribe the Instagram Business account to this app's webhook (messages field).

    Required for Instagram Business Login API — the generic App Dashboard Webhooks
    section only works for legacy Instagram Platform. Each account must subscribe
    individually via POST /{ig-user-id}/subscribed_apps.
    """
    import httpx

    version = settings.meta_graph_api_version
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{_GRAPH_BASE}/{version}/{ig_account_id}/subscribed_apps",
            params={
                "subscribed_fields": "messages",
                "access_token": access_token,
            },
        )
    if not resp.is_success:
        raise ChannelApiError(
            f"Failed to subscribe Instagram account {ig_account_id} to webhooks: "
            f"{resp.status_code} {resp.text}"
        )


async def exchange_instagram_code(
    code: str,
    state: str,
    *,
    session: AsyncSession,
    settings: Settings,
) -> ChannelConnection:
    """Exchange an Instagram OAuth code for a long-lived token and persist the connection.

    Raises:
        OAuthStateError: if the state token is invalid.
        ChannelApiError: if any Graph API call fails.
    """
    try:
        state_data = verify_state(state, secret=settings.meta_app_secret)
    except OAuthStateError:
        raise

    tenant_id = uuid.UUID(str(state_data["tenant_id"]))
    redirect_uri = f"{settings.base_url}/channels/oauth/instagram/callback"

    short_lived = await _exchange_code_for_short_lived(code, redirect_uri, settings=settings)
    long_lived, expires_in = await _exchange_short_for_long_lived(short_lived, settings=settings)
    ig_account_id, username = await _fetch_ig_account_id(long_lived, settings=settings)
    await _subscribe_ig_account(ig_account_id, long_lived, settings=settings)

    expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)

    credentials = encrypt_credentials(
        {"access_token": long_lived, "ig_user_id": ig_account_id, "username": username},
        key=settings.channel_credentials_encryption_key,
    )
    conn = ChannelConnection(
        tenant_id=tenant_id,
        channel_type="instagram",
        status="active",
        credentials_encrypted=credentials,
        expires_at=expires_at,
        connection_metadata={"ig_account_id": ig_account_id, "username": username},
    )
    session.add(conn)
    await session.commit()
    return conn
