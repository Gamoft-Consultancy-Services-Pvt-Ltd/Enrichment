"""WhatsApp Embedded Signup (Facebook Login for Business) connection handler.

NOT a redirect OAuth — the frontend calls FB.login() with config_id, gets a code,
and POSTs it to our backend. We exchange the code here and provision connections.

Flow:
  1. Exchange code → non-expiring BISU (Business Integration System User) token
  2. GET /debug_token → extract granular_scopes to find WABA IDs
  3. For each WABA: GET /{waba_id}/phone_numbers → phone number IDs
  4. POST /{waba_id}/subscribed_apps → subscribe WABA to webhooks
  5. Create one ChannelConnection per phone_number_id

Public surface:
  exchange_whatsapp_signup_code(code, *, session, settings, tenant_id) -> list[ChannelConnection]

Internal helpers (patchable in tests):
  _exchange_signup_code(code, *, settings) -> str
  _get_waba_ids(token, *, settings) -> list[str]
  _get_phone_numbers(waba_id, token, *, settings) -> list[dict]
  _subscribe_waba(waba_id, token, *, settings) -> None
"""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from core.config import Settings
from modules.lead_ingestion.crypto import encrypt_credentials
from modules.lead_ingestion.exceptions import ChannelApiError
from shared.channels.models import ChannelConnection

_GRAPH_BASE = "https://graph.facebook.com"


async def _exchange_signup_code(code: str, *, settings: Settings) -> str:
    """Exchange the Embedded Signup code for a BISU access token."""
    import httpx

    version = settings.meta_graph_api_version
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{_GRAPH_BASE}/{version}/oauth/access_token",
            params={
                "client_id": settings.meta_app_id,
                "client_secret": settings.meta_app_secret,
                "code": code,
            },
        )
    if not resp.is_success:
        raise ChannelApiError(
            f"WhatsApp signup code exchange failed: {resp.status_code} {resp.text}"
        )
    data: dict[str, Any] = resp.json()
    return str(data["access_token"])


async def _get_waba_ids(token: str, *, settings: Settings) -> list[str]:
    """Use /debug_token to discover which WABA IDs are accessible via this token."""
    import httpx

    version = settings.meta_graph_api_version
    app_token = f"{settings.meta_app_id}|{settings.meta_app_secret}"
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{_GRAPH_BASE}/{version}/debug_token",
            params={"input_token": token, "access_token": app_token},
        )
    if not resp.is_success:
        raise ChannelApiError(
            f"debug_token lookup failed: {resp.status_code} {resp.text}"
        )
    data: dict[str, Any] = resp.json()
    # granular_scopes contains objects like {"scope": "whatsapp_business_management", "target_ids": [...]}
    granular_scopes: list[dict[str, Any]] = data.get("data", {}).get("granular_scopes", [])
    waba_ids: list[str] = []
    for scope_obj in granular_scopes:
        if "whatsapp" in scope_obj.get("scope", ""):
            for tid in scope_obj.get("target_ids", []):
                waba_ids.append(str(tid))
    return list(dict.fromkeys(waba_ids))  # deduplicate, preserve order


async def _get_phone_numbers(
    waba_id: str,
    token: str,
    *,
    settings: Settings,
) -> list[dict[str, Any]]:
    """Fetch phone numbers registered under a WABA."""
    import httpx

    version = settings.meta_graph_api_version
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{_GRAPH_BASE}/{version}/{waba_id}/phone_numbers",
            params={"access_token": token},
        )
    if not resp.is_success:
        raise ChannelApiError(
            f"Failed to fetch phone numbers for WABA {waba_id}: {resp.status_code} {resp.text}"
        )
    data: dict[str, Any] = resp.json()
    phones: list[dict[str, Any]] = data.get("data", [])
    return phones


async def _subscribe_waba(waba_id: str, token: str, *, settings: Settings) -> None:
    """Subscribe a WABA to webhook events."""
    import httpx

    version = settings.meta_graph_api_version
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{_GRAPH_BASE}/{version}/{waba_id}/subscribed_apps",
            params={"access_token": token},
        )
    if not resp.is_success:
        raise ChannelApiError(
            f"Failed to subscribe WABA {waba_id} to webhooks: {resp.status_code} {resp.text}"
        )


async def exchange_whatsapp_signup_code(
    code: str,
    *,
    session: AsyncSession,
    settings: Settings,
    tenant_id: uuid.UUID,
) -> list[ChannelConnection]:
    """Exchange a WhatsApp Embedded Signup code and create ChannelConnections.

    Returns one ChannelConnection per phone number found across all WABAs.

    Raises:
        ChannelApiError: if any Graph API call fails.
    """
    token = await _exchange_signup_code(code, settings=settings)
    waba_ids = await _get_waba_ids(token, settings=settings)

    connections: list[ChannelConnection] = []
    for waba_id in waba_ids:
        await _subscribe_waba(waba_id, token, settings=settings)
        phones = await _get_phone_numbers(waba_id, token, settings=settings)
        for phone in phones:
            phone_number_id: str = phone["id"]
            display_number: str = phone.get("display_phone_number", "")
            credentials = encrypt_credentials(
                {"access_token": token, "phone_number_id": phone_number_id, "waba_id": waba_id},
                key=settings.channel_credentials_encryption_key,
            )
            conn = ChannelConnection(
                tenant_id=tenant_id,
                channel_type="whatsapp",
                status="active",
                credentials_encrypted=credentials,
                connection_metadata={
                    "phone_number_id": phone_number_id,
                    "display_phone_number": display_number,
                    "waba_id": waba_id,
                },
            )
            session.add(conn)
            connections.append(conn)

    if connections:
        await session.commit()
    return connections
