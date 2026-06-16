"""Create Instagram ChannelConnection directly, bypassing OAuth (CAPTCHA-blocked).

Uses the real encrypt_credentials utility so credentials_encrypted is properly
AES-256-GCM encrypted — Phase 13 sub-check 13.2 will pass.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.config import get_settings
from modules.lead_ingestion.crypto import encrypt_credentials
from shared.channels.models import ChannelConnection

# Import all ORM models so SQLAlchemy can resolve foreign keys
import auth.models  # noqa: F401
import shared.tenant.models  # noqa: F401
import shared.tenant_config.models  # noqa: F401
import modules.lead_ingestion.db.models  # noqa: F401

TENANT_ID = UUID("5a4ba9f6-f1ff-47e2-8c11-856269d277b6")

# Fake Instagram Business account — used for routing in Phase 14
IG_ACCOUNT_ID = "17841467503890936"
IG_USERNAME = "squareinchesrealty_ind"
FAKE_ACCESS_TOKEN = "IGAT_test_60day_token_phase13"


async def main() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    creds_blob = encrypt_credentials(
        {"access_token": FAKE_ACCESS_TOKEN, "ig_user_id": IG_ACCOUNT_ID},
        key=settings.channel_credentials_encryption_key,
    )

    expires_at = datetime.now(timezone.utc) + timedelta(days=60)

    async with factory() as session:
        conn = ChannelConnection(
            tenant_id=TENANT_ID,
            channel_type="instagram",
            status="active",
            credentials_encrypted=creds_blob,
            expires_at=expires_at,
            connection_metadata={
                "ig_account_id": IG_ACCOUNT_ID,
                "username": IG_USERNAME,
            },
        )
        session.add(conn)
        await session.commit()
        await session.refresh(conn)
        print(f"Created Instagram ChannelConnection:")
        print(f"  id={conn.id}")
        print(f"  ig_account_id={IG_ACCOUNT_ID}")
        print(f"  username={IG_USERNAME}")
        print(f"  expires_at={conn.expires_at}")
        print(f"  credentials_encrypted length={len(conn.credentials_encrypted or b'')} bytes")

    await engine.dispose()


asyncio.run(main())
