"""Integration tests: Instagram OAuth → ChannelConnection row in DB.

Patches the four internal Graph API helpers so no real HTTP calls are made.
Verifies the connection is persisted with correct metadata, expiry, and
decryptable credentials.
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from modules.lead_ingestion.crypto import decrypt_credentials
from modules.lead_ingestion.exceptions import ChannelApiError, OAuthStateError
from modules.lead_ingestion.oauth.instagram import exchange_instagram_code
from modules.lead_ingestion.oauth.state import sign_state
from shared.channels.models import ChannelConnection
from shared.tenant import service as tenant_service
from shared.tenant.schemas import BusinessType, TenantCreate


async def _make_tenant(session: AsyncSession) -> uuid.UUID:
    tenant = await tenant_service.create_tenant(
        session,
        TenantCreate(
            company_name="IG Connect Co",
            primary_contact_name="Test User",
            primary_contact_email=f"test_{uuid.uuid4().hex[:8]}@example.com",
            business_type=BusinessType.B2B,
            website_url="https://example.com",  # type: ignore[arg-type]
            pan="AAACX1234C",
            pan_holder_name="Test Holder Pvt Ltd",
            pan_dob="01/04/2019",
            consent=True,
        ),
    )
    return tenant.id


def _make_state(tenant_id: uuid.UUID) -> str:
    return sign_state(
        {"tenant_id": str(tenant_id), "channel": "instagram"},
        secret=get_settings().meta_ig_app_secret,
    )


async def test_instagram_connect_creates_connection_row(session: AsyncSession) -> None:
    tenant_id = await _make_tenant(session)
    settings = get_settings()
    state = _make_state(tenant_id)

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
            new=AsyncMock(return_value=("ig-123456", "mybusiness")),
        ),
        patch(
            "modules.lead_ingestion.oauth.instagram._subscribe_ig_account",
            new=AsyncMock(),
        ),
    ):
        conn = await exchange_instagram_code("auth-code", state, session=session, settings=settings)

    assert conn.channel_type == "instagram"
    assert conn.status == "active"
    assert conn.tenant_id == tenant_id

    row = (
        await session.execute(
            select(ChannelConnection).where(ChannelConnection.tenant_id == tenant_id)
        )
    ).scalar_one()
    assert row.id == conn.id


async def test_instagram_connect_stores_correct_metadata(session: AsyncSession) -> None:
    tenant_id = await _make_tenant(session)
    settings = get_settings()
    state = _make_state(tenant_id)

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
            new=AsyncMock(return_value=("ig-789", "coolbrand")),
        ),
        patch(
            "modules.lead_ingestion.oauth.instagram._subscribe_ig_account",
            new=AsyncMock(),
        ),
    ):
        conn = await exchange_instagram_code("auth-code", state, session=session, settings=settings)

    assert conn.connection_metadata is not None
    assert conn.connection_metadata["ig_account_id"] == "ig-789"
    assert conn.connection_metadata["username"] == "coolbrand"


async def test_instagram_connect_credentials_are_decryptable(session: AsyncSession) -> None:
    tenant_id = await _make_tenant(session)
    settings = get_settings()
    state = _make_state(tenant_id)

    with (
        patch(
            "modules.lead_ingestion.oauth.instagram._exchange_code_for_short_lived",
            new=AsyncMock(return_value="short"),
        ),
        patch(
            "modules.lead_ingestion.oauth.instagram._exchange_short_for_long_lived",
            new=AsyncMock(return_value=("ig-long-token-xyz", 5183944)),
        ),
        patch(
            "modules.lead_ingestion.oauth.instagram._fetch_ig_account_id",
            new=AsyncMock(return_value=("ig-decrypt-test", "decryptme")),
        ),
        patch(
            "modules.lead_ingestion.oauth.instagram._subscribe_ig_account",
            new=AsyncMock(),
        ),
    ):
        conn = await exchange_instagram_code("auth-code", state, session=session, settings=settings)

    assert isinstance(conn.credentials_encrypted, bytes)

    creds = decrypt_credentials(
        conn.credentials_encrypted,
        key=settings.channel_credentials_encryption_key,
    )
    assert creds["access_token"] == "ig-long-token-xyz"
    assert creds["ig_user_id"] == "ig-decrypt-test"
    assert creds["username"] == "decryptme"


async def test_instagram_connect_sets_expiry(session: AsyncSession) -> None:
    tenant_id = await _make_tenant(session)
    settings = get_settings()
    state = _make_state(tenant_id)
    before = datetime.now(UTC)

    with (
        patch(
            "modules.lead_ingestion.oauth.instagram._exchange_code_for_short_lived",
            new=AsyncMock(return_value="short"),
        ),
        patch(
            "modules.lead_ingestion.oauth.instagram._exchange_short_for_long_lived",
            new=AsyncMock(return_value=("long", 5183944)),  # ~60 days in seconds
        ),
        patch(
            "modules.lead_ingestion.oauth.instagram._fetch_ig_account_id",
            new=AsyncMock(return_value=("ig-exp", "expuser")),
        ),
        patch(
            "modules.lead_ingestion.oauth.instagram._subscribe_ig_account",
            new=AsyncMock(),
        ),
    ):
        conn = await exchange_instagram_code("auth-code", state, session=session, settings=settings)

    assert conn.expires_at is not None
    # expires_at should be ~60 days from now — more than 59 days in the future
    from datetime import timedelta

    assert conn.expires_at > before + timedelta(days=59)


async def test_instagram_connect_invalid_state_raises(session: AsyncSession) -> None:
    settings = get_settings()

    with pytest.raises(OAuthStateError):
        await exchange_instagram_code(
            "auth-code", "invalid.state", session=session, settings=settings
        )


async def test_instagram_connect_api_failure_raises(session: AsyncSession) -> None:
    tenant_id = await _make_tenant(session)
    settings = get_settings()
    state = _make_state(tenant_id)

    with (
        patch(
            "modules.lead_ingestion.oauth.instagram._exchange_code_for_short_lived",
            new=AsyncMock(side_effect=ChannelApiError("Instagram API down")),
        ),
    ):
        with pytest.raises(ChannelApiError):
            await exchange_instagram_code("auth-code", state, session=session, settings=settings)
