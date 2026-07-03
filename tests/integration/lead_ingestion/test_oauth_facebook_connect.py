"""Integration tests: Facebook OAuth → ChannelConnection rows in DB.

Patches the four internal Graph API helpers so no real HTTP calls are made.
Verifies page connections are persisted with correct metadata and decryptable credentials.
"""

import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from modules.lead_ingestion.crypto import decrypt_credentials
from modules.lead_ingestion.exceptions import OAuthStateError
from modules.lead_ingestion.oauth.facebook import exchange_facebook_code
from modules.lead_ingestion.oauth.state import sign_state
from shared.channels.models import ChannelConnection
from shared.tenant import service as tenant_service
from shared.tenant.schemas import BusinessType, TenantCreate


async def _make_tenant(session: AsyncSession) -> uuid.UUID:
    tenant = await tenant_service.create_tenant(
        session,
        TenantCreate(
            company_name="FB Connect Co",
            primary_contact_name="Test User",
            primary_contact_email=f"test_{uuid.uuid4().hex[:8]}@example.com",
            business_type=BusinessType.B2B,
            website_url="https://example.com",  # type: ignore[arg-type]
        ),
    )
    return tenant.id


def _make_state(tenant_id: uuid.UUID) -> str:
    return sign_state(
        {"tenant_id": str(tenant_id), "channel": "facebook"},
        secret=get_settings().meta_app_secret,
    )


def _fake_accounts() -> list[dict[str, Any]]:
    return [
        {"id": "page-001", "name": "Acme Real Estate", "access_token": "page-token-001"},
        {"id": "page-002", "name": "Acme Rentals", "access_token": "page-token-002"},
    ]


async def test_facebook_connect_creates_one_connection_per_page(
    session: AsyncSession,
) -> None:
    tenant_id = await _make_tenant(session)
    settings = get_settings()
    state = _make_state(tenant_id)

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
            new=AsyncMock(return_value=_fake_accounts()),
        ),
        patch(
            "modules.lead_ingestion.oauth.facebook._subscribe_page_webhooks",
            new=AsyncMock(),
        ),
    ):
        connections = await exchange_facebook_code(
            "auth-code", state, session=session, settings=settings
        )

    assert len(connections) == 2

    rows = (
        (
            await session.execute(
                select(ChannelConnection).where(ChannelConnection.tenant_id == tenant_id)
            )
        )
        .scalars()
        .all()
    )

    assert len(rows) == 2
    assert all(r.channel_type == "facebook" for r in rows)
    assert all(r.status == "active" for r in rows)
    assert all(r.tenant_id == tenant_id for r in rows)


async def test_facebook_connect_stores_correct_page_metadata(
    session: AsyncSession,
) -> None:
    tenant_id = await _make_tenant(session)
    settings = get_settings()
    state = _make_state(tenant_id)

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
            new=AsyncMock(
                return_value=[{"id": "pg-xyz", "name": "My Page", "access_token": "tok-xyz"}]
            ),
        ),
        patch(
            "modules.lead_ingestion.oauth.facebook._subscribe_page_webhooks",
            new=AsyncMock(),
        ),
    ):
        connections = await exchange_facebook_code(
            "auth-code", state, session=session, settings=settings
        )

    conn = connections[0]
    assert conn.connection_metadata is not None
    assert conn.connection_metadata["page_id"] == "pg-xyz"
    assert conn.connection_metadata["page_name"] == "My Page"


async def test_facebook_connect_credentials_are_decryptable(
    session: AsyncSession,
) -> None:
    tenant_id = await _make_tenant(session)
    settings = get_settings()
    state = _make_state(tenant_id)

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
            new=AsyncMock(
                return_value=[
                    {"id": "pg-secret", "name": "Secret Page", "access_token": "secret-page-token"}
                ]
            ),
        ),
        patch(
            "modules.lead_ingestion.oauth.facebook._subscribe_page_webhooks",
            new=AsyncMock(),
        ),
    ):
        connections = await exchange_facebook_code(
            "auth-code", state, session=session, settings=settings
        )

    conn = connections[0]
    assert isinstance(conn.credentials_encrypted, bytes)

    creds = decrypt_credentials(
        conn.credentials_encrypted,
        key=settings.channel_credentials_encryption_key,
    )
    assert creds["page_access_token"] == "secret-page-token"
    assert creds["page_id"] == "pg-secret"


async def test_facebook_connect_invalid_state_raises(session: AsyncSession) -> None:
    settings = get_settings()
    import pytest

    with pytest.raises(OAuthStateError):
        await exchange_facebook_code(
            "auth-code", "bad.state.token", session=session, settings=settings
        )
