"""Directly process the Phase 12 Facebook DM without going through ARQ."""

import asyncio
from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Import all ORM models so SQLAlchemy can resolve foreign keys
import auth.models  # noqa: F401
import modules.lead_ingestion.db.models  # noqa: F401
import shared.channels.models  # noqa: F401
import shared.tenant.models  # noqa: F401
import shared.tenant_config.models  # noqa: F401
from core.config import get_settings
from modules.lead_ingestion.normaliser import normalise_facebook_dm
from modules.lead_ingestion.pipeline import run_capture_message

TENANT_ID = UUID("5a4ba9f6-f1ff-47e2-8c11-856269d277b6")
CHANNEL_CONNECTION_ID = UUID("f64b09c0-e9c5-41f0-8711-e6537234ae2b")

PAYLOAD = {
    "object": "page",
    "entry": [
        {
            "id": "1346146538572443",
            "messaging": [
                {
                    "sender": {"id": "4567890123"},
                    "recipient": {"id": "1346146538572443"},
                    "timestamp": 1700000100,
                    "message": {
                        "mid": "m_sprint4_fb_dm_001",
                        "text": "Hi I want to know more about your product features and pricing",
                    },
                }
            ],
        }
    ],
}


async def main() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        event = normalise_facebook_dm(
            PAYLOAD,
            tenant_id=TENANT_ID,
            channel_connection_id=CHANNEL_CONNECTION_ID,
        )
        lead, received = await run_capture_message(session, event)
        print(
            f"lead.id={lead.id} pipeline_stage={lead.pipeline_stage} event={'received' if received else 'duplicate/noise'}"
        )
    await engine.dispose()


asyncio.run(main())
