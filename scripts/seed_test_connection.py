"""Seed a test tenant + Facebook ChannelConnection for manual webhook testing.

Run with:  uv run python scripts/seed_test_connection.py
"""

import asyncio
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PAGE_ID = "1346146538572443"
PAGE_NAME = "Enrichment Testing"
PAGE_ACCESS_TOKEN = (
    "EAAVPUDwkbawBRZC4YgpbDMJxMZB2a4a0V9ylgHo0Y3hIDHHQO1x36TF8uyN"
    "LPcaYppHesMNJBGZA6bM2SptEU0mbJI0gY4LNtVY3FGHFpRNZAKRf9RpIDMW"
    "1KMPuBuauPtbxLZCswdnFdKLhVVL29YkApNqcEdUZC0RGZCXqLT9TeMg8ZAq6"
    "SPATEn2HjomgMZBCeW6u8mDJbOVsZCmVIpeuEkBmJNgrls3CphXUcmeuBt"
)
# Token above is from Graph API Explorer "Enrichment Testing" page session 2026-06-19.
# Re-generate via Explorer if it expires (short-lived tokens ~1h).


async def main() -> None:
    from core.config import get_settings
    from core.db import async_session_factory, engine
    from modules.lead_ingestion.crypto import encrypt_credentials
    from shared.channels.models import ChannelConnection
    from shared.tenant import service as tenant_service
    from shared.tenant.schemas import BusinessType, TenantCreate
    from shared.tenant_config import service as config_service
    from shared.tenant_config.schemas import TenantConfigCreate

    settings = get_settings()

    async with async_session_factory() as session:
        # 1. Create tenant
        tenant = await tenant_service.create_tenant(
            session,
            TenantCreate(
                company_name="Meta Test Co",
                primary_contact_name="Test Admin",
                primary_contact_email="test@metatestco.example.com",
                business_type=BusinessType.B2B,
                website_url="https://metatestco.example.com",  # type: ignore[arg-type]
            ),
        )
        print(f"Created tenant: {tenant.id}  ({tenant.company_name})")

        await tenant_service.activate_tenant(session, tenant.id)
        print("Activated tenant")

        # 2. Create active tenant config (needed for scoring later)
        await config_service.create_active(
            session,
            tenant.id,
            TenantConfigCreate.model_validate({
                "business_profile": {"summary": "B2B SaaS testing"},
                "icp": {"summary": "SMB decision-makers"},
                "signals": [
                    {"id": "fit_1", "dimension": "FIT", "question": "In target segment?"},
                    {"id": "intent_1", "dimension": "INTENT", "question": "Active buyer?"},
                    {"id": "eng_1", "dimension": "ENGAGEMENT", "question": "Engaged with ad?"},
                    {"id": "beh_1", "dimension": "BEHAVIOUR", "question": "Site visit?"},
                    {"id": "ctx_1", "dimension": "CONTEXT", "question": "Recent trigger?"},
                ],
                "weights": {"fit": 0.2, "intent": 0.2, "engagement": 0.2, "behaviour": 0.2, "context": 0.2},
                "thresholds": {"hot": 80.0, "warm": 55.0},
            }),
        )
        print("Created tenant config")

        # 3. Encrypt page token and create ChannelConnection
        credentials = encrypt_credentials(
            {"page_access_token": PAGE_ACCESS_TOKEN, "page_id": PAGE_ID},
            key=settings.channel_credentials_encryption_key,
        )
        conn = ChannelConnection(
            tenant_id=tenant.id,
            channel_type="facebook",
            status="active",
            credentials_encrypted=credentials,
            connection_metadata={"page_id": PAGE_ID, "page_name": PAGE_NAME},
        )
        session.add(conn)
        await session.commit()
        await session.refresh(conn)
        print(f"Created ChannelConnection: {conn.id}  page_id={PAGE_ID}")
        print(f"\nReady. Tenant ID: {tenant.id}")
        print(f"Connection ID: {conn.id}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
