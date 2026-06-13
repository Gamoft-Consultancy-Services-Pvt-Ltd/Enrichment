"""Daily ARQ cron job: refresh long-lived Instagram tokens before they expire.

Logic per connection:
  - If now >= expires_at              → set status='expired', commit, skip refresh.
  - If expires_at - now < 7 days      → call the Instagram token refresh endpoint,
                                        store new encrypted credentials + new expires_at.
  - Otherwise (expires_at - now >= 7d) → token is fresh, no action.

Public surface:
  refresh_instagram_tokens(session) — called by the ARQ worker on a daily schedule.

Internal helpers (patchable in tests):
  _now_utc()                           — returns datetime.now(UTC); mockable clock.
  _call_refresh_api(access_token, ...) — makes the actual HTTP call to Instagram.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Imported at module level so tests can patch at this qualified name.
from modules.lead_ingestion.crypto import decrypt_credentials, encrypt_credentials
from shared.channels.models import ChannelConnection

_REFRESH_WINDOW = timedelta(days=7)

# Instagram Graph API refresh endpoint
_REFRESH_URL = "https://graph.instagram.com/refresh_access_token"


def _now_utc() -> datetime:
    """Return the current UTC datetime. Isolated for test patching."""
    return datetime.now(UTC)


async def _call_refresh_api(
    access_token: str,
    *,
    app_id: str = "",
    app_secret: str = "",
) -> dict[str, Any]:
    """Call the Instagram Graph API to refresh a long-lived token.

    Returns a dict containing at least 'access_token' and 'expires_in'.
    Raises ChannelApiError on non-2xx responses.
    """
    import httpx

    from modules.lead_ingestion.exceptions import ChannelApiError

    params = {
        "grant_type": "ig_refresh_token",
        "access_token": access_token,
    }
    async with httpx.AsyncClient() as client:
        resp = await client.get(_REFRESH_URL, params=params)

    if not resp.is_success:
        raise ChannelApiError(f"Instagram token refresh failed: {resp.status_code} {resp.text}")

    data: dict[str, Any] = resp.json()
    return data


async def refresh_instagram_tokens(
    session: AsyncSession,
    *,
    encryption_key: str = "",
) -> None:
    """Refresh all active Instagram tokens within the refresh window.

    Designed to run as a daily ARQ cron job.

    Args:
        session: An active AsyncSession (provided by the worker).
        encryption_key: AES-256 key for credential en/decryption.
                        Defaults to settings value; supply in tests via arg or patch.
    """
    from core.config import get_settings

    key = encryption_key or get_settings().channel_credentials_encryption_key
    now = _now_utc()

    result = await session.execute(
        select(ChannelConnection).where(
            ChannelConnection.channel_type == "instagram",
            ChannelConnection.status == "active",
        )
    )
    connections = result.scalars().all()

    for conn in connections:
        if conn.expires_at is None:
            continue

        # Ensure expires_at is timezone-aware for comparison
        expires_at = conn.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)

        if now >= expires_at:
            # Token already expired — mark it and skip the API call.
            conn.status = "expired"
            await session.commit()
            continue

        time_remaining = expires_at - now
        if time_remaining < _REFRESH_WINDOW:
            # Within the 7-day refresh window — call the refresh API.
            existing_creds: dict[str, Any] = {}
            if conn.credentials_encrypted and key:
                try:
                    existing_creds = decrypt_credentials(conn.credentials_encrypted, key=key)
                except Exception:
                    # If we can't decrypt, mark as expired and skip.
                    conn.status = "expired"
                    await session.commit()
                    continue

            current_token: str = existing_creds.get("access_token", "")
            new_data = await _call_refresh_api(current_token)

            # Merge the new access_token into the existing credentials dict.
            existing_creds["access_token"] = new_data["access_token"]
            if key:
                conn.credentials_encrypted = encrypt_credentials(existing_creds, key=key)

            # Update expires_at based on the new expires_in (seconds from now).
            expires_in: int = int(new_data.get("expires_in", 5183944))
            conn.expires_at = now + timedelta(seconds=expires_in)

            await session.commit()
        # else: time_remaining >= 7 days → no action needed
