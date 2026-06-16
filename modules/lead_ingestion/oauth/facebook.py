"""Facebook OAuth for page-level connections (Messenger DMs + Instagram DMs).

3-step token chain on callback:
  code → short-lived user token → long-lived user token → Page Access Token (non-expiring)

One Facebook ChannelConnection is created per Facebook Page granted by the user.
If a Page has a connected Instagram Business account, one additional Instagram
ChannelConnection is created using the same non-expiring Page access token — this
avoids the 60-day expiry that affects Instagram Business Login tokens.

Public surface:
  build_facebook_auth_url(tenant_id, *, settings) -> str
  exchange_facebook_code(code, state, *, session, settings) -> list[ChannelConnection]

Internal helpers (patchable in tests):
  _exchange_code_for_short_lived(code, redirect_uri, *, settings) -> str
  _exchange_short_for_long_lived(short_lived, *, settings) -> str
  _fetch_page_accounts(long_lived, *, settings) -> list[dict]
  _subscribe_page_webhooks(page_id, page_token, *, settings) -> None
  _fetch_connected_ig_account(page_id, page_token, *, settings) -> str | None
  _subscribe_ig_via_page_token(ig_account_id, page_token, *, settings) -> None
"""

import uuid
from typing import Any
from urllib.parse import urlencode

from sqlalchemy.ext.asyncio import AsyncSession

from core.config import Settings
from modules.lead_ingestion.crypto import encrypt_credentials
from modules.lead_ingestion.exceptions import ChannelApiError, OAuthStateError
from modules.lead_ingestion.oauth.state import sign_state, verify_state
from shared.channels.models import ChannelConnection

_AUTH_BASE = "https://www.facebook.com/dialog/oauth"
_GRAPH_BASE = "https://graph.facebook.com"

_SCOPES = ",".join(
    [
        "pages_show_list",
        "pages_manage_metadata",
        "pages_messaging",
        "pages_read_engagement",
        "instagram_manage_messages",
        "instagram_basic",
    ]
)


def build_facebook_auth_url(tenant_id: uuid.UUID, *, settings: Settings) -> str:
    """Build the Facebook OAuth redirect URL with an HMAC-signed state parameter."""
    state = sign_state(
        {"tenant_id": str(tenant_id), "channel": "facebook"},
        secret=settings.meta_app_secret,
    )
    redirect_uri = f"{settings.base_url}/channels/oauth/facebook/callback"
    params = {
        "client_id": settings.meta_app_id,
        "redirect_uri": redirect_uri,
        "scope": _SCOPES,
        "state": state,
    }
    return f"{_AUTH_BASE}?{urlencode(params)}"


async def _exchange_code_for_short_lived(
    code: str,
    redirect_uri: str,
    *,
    settings: Settings,
) -> str:
    """Exchange an auth code for a short-lived user token."""
    import httpx

    version = settings.meta_graph_api_version
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{_GRAPH_BASE}/{version}/oauth/access_token",
            params={
                "client_id": settings.meta_app_id,
                "client_secret": settings.meta_app_secret,
                "redirect_uri": redirect_uri,
                "code": code,
            },
        )
    if not resp.is_success:
        raise ChannelApiError(
            f"Facebook code exchange failed: {resp.status_code} {resp.text}"
        )
    data: dict[str, Any] = resp.json()
    return str(data["access_token"])


async def _exchange_short_for_long_lived(
    short_lived: str,
    *,
    settings: Settings,
) -> str:
    """Exchange a short-lived user token for a long-lived one (60 days)."""
    import httpx

    version = settings.meta_graph_api_version
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{_GRAPH_BASE}/{version}/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": settings.meta_app_id,
                "client_secret": settings.meta_app_secret,
                "fb_exchange_token": short_lived,
            },
        )
    if not resp.is_success:
        raise ChannelApiError(
            f"Facebook long-lived token exchange failed: {resp.status_code} {resp.text}"
        )
    data: dict[str, Any] = resp.json()
    return str(data["access_token"])


async def _fetch_page_accounts(
    long_lived_token: str,
    *,
    settings: Settings,
) -> list[dict[str, Any]]:
    """Fetch page accounts the user granted access to; returns non-empty list."""
    import httpx

    version = settings.meta_graph_api_version
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{_GRAPH_BASE}/{version}/me/accounts",
            params={"access_token": long_lived_token},
        )
    if not resp.is_success:
        raise ChannelApiError(
            f"Failed to fetch Facebook page accounts: {resp.status_code} {resp.text}"
        )
    data: dict[str, Any] = resp.json()
    accounts: list[dict[str, Any]] = data.get("data", [])
    if not accounts:
        raise ChannelApiError("No Facebook pages found for this account")
    return accounts


async def _subscribe_page_webhooks(
    page_id: str,
    page_token: str,
    *,
    settings: Settings,
) -> None:
    """Subscribe a page to messages webhook events."""
    import httpx

    version = settings.meta_graph_api_version
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{_GRAPH_BASE}/{version}/{page_id}/subscribed_apps",
            params={
                "subscribed_fields": "messages",
                "access_token": page_token,
            },
        )
    if not resp.is_success:
        raise ChannelApiError(
            f"Failed to subscribe page {page_id} to webhooks: {resp.status_code} {resp.text}"
        )


async def _fetch_connected_ig_account(
    page_id: str,
    page_token: str,
    *,
    settings: Settings,
) -> str | None:
    """Return the Instagram Business Account ID connected to this Facebook Page, or None.

    Uses the non-expiring page token — no separate Instagram OAuth needed.
    Returns None (not an error) when the page has no linked Instagram account or
    when the Graph API call fails, so callers can skip gracefully.
    """
    import httpx

    version = settings.meta_graph_api_version
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{_GRAPH_BASE}/{version}/{page_id}",
            params={
                "fields": "instagram_business_account",
                "access_token": page_token,
            },
        )
    if not resp.is_success:
        return None
    data: dict[str, Any] = resp.json()
    ig_account = data.get("instagram_business_account")
    if not ig_account:
        return None
    return str(ig_account["id"])


async def _subscribe_ig_via_page_token(
    ig_account_id: str,
    page_token: str,
    *,
    settings: Settings,
) -> None:
    """Subscribe an Instagram Business account to messages webhooks using the page token."""
    import httpx

    version = settings.meta_graph_api_version
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{_GRAPH_BASE}/{version}/{ig_account_id}/subscribed_apps",
            params={
                "subscribed_fields": "messages",
                "access_token": page_token,
            },
        )
    if not resp.is_success:
        raise ChannelApiError(
            f"Failed to subscribe Instagram account {ig_account_id} to webhooks: "
            f"{resp.status_code} {resp.text}"
        )


async def exchange_facebook_code(
    code: str,
    state: str,
    *,
    session: AsyncSession,
    settings: Settings,
) -> list[ChannelConnection]:
    """Exchange a Facebook OAuth code for page access tokens and persist connections.

    Verifies the HMAC-signed state, runs the 3-step token chain, subscribes each
    page to webhook events, and returns one ChannelConnection per page.

    If a page has a connected Instagram Business account, also creates a non-expiring
    Instagram ChannelConnection using the same page token (expires_at=None).

    Raises:
        OAuthStateError: if the state token is invalid.
        ChannelApiError: if any Graph API call fails.
    """
    try:
        state_data = verify_state(state, secret=settings.meta_app_secret)
    except OAuthStateError:
        raise

    tenant_id = uuid.UUID(str(state_data["tenant_id"]))
    redirect_uri = f"{settings.base_url}/channels/oauth/facebook/callback"

    short_lived = await _exchange_code_for_short_lived(code, redirect_uri, settings=settings)
    long_lived = await _exchange_short_for_long_lived(short_lived, settings=settings)
    accounts = await _fetch_page_accounts(long_lived, settings=settings)

    connections: list[ChannelConnection] = []
    for account in accounts:
        page_id: str = account["id"]
        page_name: str = account.get("name", "")
        page_token: str = account["access_token"]

        await _subscribe_page_webhooks(page_id, page_token, settings=settings)

        fb_credentials = encrypt_credentials(
            {"page_access_token": page_token, "page_id": page_id},
            key=settings.channel_credentials_encryption_key,
        )
        fb_conn = ChannelConnection(
            tenant_id=tenant_id,
            channel_type="facebook",
            status="active",
            credentials_encrypted=fb_credentials,
            connection_metadata={"page_id": page_id, "page_name": page_name},
        )
        session.add(fb_conn)
        connections.append(fb_conn)

        ig_account_id = await _fetch_connected_ig_account(page_id, page_token, settings=settings)
        if ig_account_id:
            await _subscribe_ig_via_page_token(ig_account_id, page_token, settings=settings)
            ig_credentials = encrypt_credentials(
                {"page_access_token": page_token, "ig_account_id": ig_account_id},
                key=settings.channel_credentials_encryption_key,
            )
            ig_conn = ChannelConnection(
                tenant_id=tenant_id,
                channel_type="instagram",
                status="active",
                credentials_encrypted=ig_credentials,
                expires_at=None,
                connection_metadata={"ig_account_id": ig_account_id},
            )
            session.add(ig_conn)
            connections.append(ig_conn)

    await session.commit()
    return connections
