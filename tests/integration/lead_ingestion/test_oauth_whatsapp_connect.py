"""Integration tests: WhatsApp Embedded Signup → ChannelConnection rows in DB.

Patches the four internal Graph API helpers so no real HTTP calls are made.
Verifies that the service layer correctly creates, persists, and commits
ChannelConnection rows, and that the stored credentials can be decrypted.
"""

import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from modules.lead_ingestion.crypto import decrypt_credentials
from modules.lead_ingestion.oauth.whatsapp import exchange_whatsapp_signup_code
from shared.channels.models import ChannelConnection
from shared.tenant import service as tenant_service
from shared.tenant.schemas import BusinessType, TenantCreate


async def _make_tenant(session: AsyncSession) -> uuid.UUID:
    tenant = await tenant_service.create_tenant(
        session,
        TenantCreate(
            company_name="WA Connect Co",
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


def _fake_phones() -> list[dict[str, Any]]:
    return [
        {"id": "phone-001", "display_phone_number": "+91 98765 43210"},
        {"id": "phone-002", "display_phone_number": "+91 87654 32109"},
    ]


async def test_whatsapp_connect_creates_one_connection_per_phone(
    session: AsyncSession,
) -> None:
    tenant_id = await _make_tenant(session)
    settings = get_settings()

    with (
        patch(
            "modules.lead_ingestion.oauth.whatsapp._exchange_signup_code",
            new=AsyncMock(return_value="bisu-token-xyz"),
        ),
        patch(
            "modules.lead_ingestion.oauth.whatsapp._get_waba_ids",
            new=AsyncMock(return_value=["waba-001"]),
        ),
        patch(
            "modules.lead_ingestion.oauth.whatsapp._subscribe_waba",
            new=AsyncMock(),
        ),
        patch(
            "modules.lead_ingestion.oauth.whatsapp._get_phone_numbers",
            new=AsyncMock(return_value=_fake_phones()),
        ),
    ):
        connections = await exchange_whatsapp_signup_code(
            "test-code",
            session=session,
            settings=settings,
            tenant_id=tenant_id,
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
    assert all(r.channel_type == "whatsapp" for r in rows)
    assert all(r.status == "active" for r in rows)
    assert all(r.tenant_id == tenant_id for r in rows)


async def test_whatsapp_connect_stores_correct_metadata(
    session: AsyncSession,
) -> None:
    tenant_id = await _make_tenant(session)
    settings = get_settings()

    with (
        patch(
            "modules.lead_ingestion.oauth.whatsapp._exchange_signup_code",
            new=AsyncMock(return_value="bisu-token-abc"),
        ),
        patch(
            "modules.lead_ingestion.oauth.whatsapp._get_waba_ids",
            new=AsyncMock(return_value=["waba-999"]),
        ),
        patch(
            "modules.lead_ingestion.oauth.whatsapp._subscribe_waba",
            new=AsyncMock(),
        ),
        patch(
            "modules.lead_ingestion.oauth.whatsapp._get_phone_numbers",
            new=AsyncMock(
                return_value=[{"id": "phone-999", "display_phone_number": "+1 555 000 0001"}]
            ),
        ),
    ):
        connections = await exchange_whatsapp_signup_code(
            "test-code",
            session=session,
            settings=settings,
            tenant_id=tenant_id,
        )

    conn = connections[0]
    assert conn.connection_metadata is not None
    assert conn.connection_metadata["phone_number_id"] == "phone-999"
    assert conn.connection_metadata["waba_id"] == "waba-999"
    assert conn.connection_metadata["display_phone_number"] == "+1 555 000 0001"


async def test_whatsapp_connect_credentials_are_encrypted_and_decryptable(
    session: AsyncSession,
) -> None:
    tenant_id = await _make_tenant(session)
    settings = get_settings()

    with (
        patch(
            "modules.lead_ingestion.oauth.whatsapp._exchange_signup_code",
            new=AsyncMock(return_value="super-secret-bisu-token"),
        ),
        patch(
            "modules.lead_ingestion.oauth.whatsapp._get_waba_ids",
            new=AsyncMock(return_value=["waba-111"]),
        ),
        patch(
            "modules.lead_ingestion.oauth.whatsapp._subscribe_waba",
            new=AsyncMock(),
        ),
        patch(
            "modules.lead_ingestion.oauth.whatsapp._get_phone_numbers",
            new=AsyncMock(
                return_value=[{"id": "ph-111", "display_phone_number": "+44 7700 900000"}]
            ),
        ),
    ):
        connections = await exchange_whatsapp_signup_code(
            "test-code",
            session=session,
            settings=settings,
            tenant_id=tenant_id,
        )

    conn = connections[0]
    assert isinstance(conn.credentials_encrypted, bytes)
    assert len(conn.credentials_encrypted) > 12  # nonce + ciphertext

    creds = decrypt_credentials(
        conn.credentials_encrypted,
        key=settings.channel_credentials_encryption_key,
    )
    assert creds["access_token"] == "super-secret-bisu-token"
    assert creds["phone_number_id"] == "ph-111"
    assert creds["waba_id"] == "waba-111"


async def test_whatsapp_connect_multiple_wabas_creates_connection_per_phone(
    session: AsyncSession,
) -> None:
    """Two WABAs each with one phone → two connections."""
    tenant_id = await _make_tenant(session)
    settings = get_settings()

    phones_by_waba = {
        "waba-A": [{"id": "ph-A1", "display_phone_number": "+1 111 111 1111"}],
        "waba-B": [{"id": "ph-B1", "display_phone_number": "+1 222 222 2222"}],
    }

    async def _fake_get_phones(waba_id: str, token: str, *, settings: Any) -> list[dict[str, Any]]:
        return phones_by_waba[waba_id]

    with (
        patch(
            "modules.lead_ingestion.oauth.whatsapp._exchange_signup_code",
            new=AsyncMock(return_value="multi-waba-token"),
        ),
        patch(
            "modules.lead_ingestion.oauth.whatsapp._get_waba_ids",
            new=AsyncMock(return_value=["waba-A", "waba-B"]),
        ),
        patch(
            "modules.lead_ingestion.oauth.whatsapp._subscribe_waba",
            new=AsyncMock(),
        ),
        patch(
            "modules.lead_ingestion.oauth.whatsapp._get_phone_numbers",
            new=AsyncMock(side_effect=_fake_get_phones),
        ),
    ):
        connections = await exchange_whatsapp_signup_code(
            "test-code",
            session=session,
            settings=settings,
            tenant_id=tenant_id,
        )

    assert len(connections) == 2
    waba_ids = {c.connection_metadata["waba_id"] for c in connections if c.connection_metadata}
    assert waba_ids == {"waba-A", "waba-B"}
