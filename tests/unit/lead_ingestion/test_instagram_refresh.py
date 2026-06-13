"""Unit tests for instagram_token_refresh — no DB, no network.

Three clock scenarios:
  1. expires_at - 6 days  → within the 7-day refresh window → refresh API called.
  2. expires_at - 10 days → outside the 7-day window       → no action taken.
  3. expires_at + 1 day   → already expired                → status set to 'expired',
                                                              refresh NOT called.

The DB session and the Meta Graph API call are both mocked so this runs entirely
in memory.  The ChannelConnection ORM model is constructed directly (no real DB).
"""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from modules.lead_ingestion.instagram_token_refresh import refresh_instagram_tokens
from shared.channels.models import ChannelConnection

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE_EXPIRES = datetime(2026, 7, 1, 12, 0, 0, tzinfo=UTC)


def _make_conn(expires_at: datetime) -> ChannelConnection:
    """Build a minimal ChannelConnection object without touching the DB."""
    conn = MagicMock(spec=ChannelConnection)
    conn.id = uuid.uuid4()
    conn.tenant_id = uuid.uuid4()
    conn.channel_type = "instagram"
    conn.status = "active"
    conn.expires_at = expires_at
    conn.credentials_encrypted = b"dummy-encrypted-bytes"
    return conn


def _mock_session(connections: list[ChannelConnection]) -> AsyncMock:
    """Return an AsyncSession mock whose execute().scalars().all() yields connections."""
    session = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = connections
    session.execute.return_value = result
    return session


# ---------------------------------------------------------------------------
# Scenario 1: expires_at - 6 days → within refresh window
# ---------------------------------------------------------------------------


async def test_within_refresh_window_calls_refresh_api() -> None:
    """When now = expires_at - 6d, the token is within the 7-day window; refresh fires."""
    now = _BASE_EXPIRES - timedelta(days=6)
    conn = _make_conn(_BASE_EXPIRES)
    session = _mock_session([conn])

    new_token_payload = {"access_token": "new-token-abc", "expires_in": 5183944}

    with (
        patch(
            "modules.lead_ingestion.instagram_token_refresh._now_utc",
            return_value=now,
        ),
        patch(
            "modules.lead_ingestion.instagram_token_refresh._call_refresh_api",
            new=AsyncMock(return_value=new_token_payload),
        ) as mock_refresh,
        patch(
            "modules.lead_ingestion.instagram_token_refresh.decrypt_credentials",
            return_value={"access_token": "old-token"},
        ),
        patch(
            "modules.lead_ingestion.instagram_token_refresh.encrypt_credentials",
            return_value=b"new-encrypted",
        ),
    ):
        await refresh_instagram_tokens(session)

    mock_refresh.assert_called_once()
    # Connection status must remain 'active' (not expired)
    assert conn.status == "active"


# ---------------------------------------------------------------------------
# Scenario 2: expires_at - 10 days → outside refresh window, no action
# ---------------------------------------------------------------------------


async def test_outside_refresh_window_no_api_call() -> None:
    """When now = expires_at - 10d, the token is fresh; no refresh is triggered."""
    now = _BASE_EXPIRES - timedelta(days=10)
    conn = _make_conn(_BASE_EXPIRES)
    session = _mock_session([conn])

    with (
        patch(
            "modules.lead_ingestion.instagram_token_refresh._now_utc",
            return_value=now,
        ),
        patch(
            "modules.lead_ingestion.instagram_token_refresh._call_refresh_api",
            new=AsyncMock(return_value={"access_token": "should-not-be-called"}),
        ) as mock_refresh,
    ):
        await refresh_instagram_tokens(session)

    mock_refresh.assert_not_called()
    assert conn.status == "active"


# ---------------------------------------------------------------------------
# Scenario 3: expires_at + 1 day → already expired
# ---------------------------------------------------------------------------


async def test_already_expired_sets_status_expired_no_refresh() -> None:
    """When now > expires_at, the token is expired; status set to 'expired', no API call."""
    now = _BASE_EXPIRES + timedelta(days=1)
    conn = _make_conn(_BASE_EXPIRES)
    session = _mock_session([conn])

    with (
        patch(
            "modules.lead_ingestion.instagram_token_refresh._now_utc",
            return_value=now,
        ),
        patch(
            "modules.lead_ingestion.instagram_token_refresh._call_refresh_api",
            new=AsyncMock(return_value={"access_token": "should-not-be-called"}),
        ) as mock_refresh,
    ):
        await refresh_instagram_tokens(session)

    mock_refresh.assert_not_called()
    assert conn.status == "expired"
    session.commit.assert_called_once()


# ---------------------------------------------------------------------------
# Edge: no active instagram connections — function completes without error
# ---------------------------------------------------------------------------


async def test_no_connections_is_noop() -> None:
    """If no active Instagram connections exist, the function returns without error."""
    session = _mock_session([])

    with patch(
        "modules.lead_ingestion.instagram_token_refresh._call_refresh_api",
        new=AsyncMock(),
    ) as mock_refresh:
        await refresh_instagram_tokens(session)  # must not raise

    mock_refresh.assert_not_called()
