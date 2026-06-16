"""Directly process the Phase 14 Instagram DM without going through ARQ."""
import asyncio
from uuid import UUID
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.config import get_settings
from modules.lead_ingestion.normaliser import normalise_instagram_dm
from modules.lead_ingestion.pipeline import run_capture_message

# Import all ORM models so SQLAlchemy can resolve foreign keys
import auth.models  # noqa: F401
import shared.tenant.models  # noqa: F401
import shared.tenant_config.models  # noqa: F401
import shared.channels.models  # noqa: F401
import modules.lead_ingestion.db.models  # noqa: F401

TENANT_ID = UUID("5a4ba9f6-f1ff-47e2-8c11-856269d277b6")
CHANNEL_CONNECTION_ID = UUID("0a539ffc-c795-40a1-ad74-e511dee61182")

PAYLOAD = {
    "object": "instagram",
    "entry": [{
        "id": "17841467503890936",
        "messaging": [{
            "sender": {"id": "7890123456"},
            "recipient": {"id": "17841467503890936"},
            "timestamp": 1700000200,
            "message": {
                "mid": "m_sprint4_ig_dm_001",
                "text": "Hello I am interested in your services and would like to learn more"
            }
        }]
    }]
}


async def main() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        event = normalise_instagram_dm(
            PAYLOAD,
            tenant_id=TENANT_ID,
            channel_connection_id=CHANNEL_CONNECTION_ID,
        )
        lead, received = await run_capture_message(session, event)
        print(f"lead.id={lead.id} pipeline_stage={lead.pipeline_stage} event={'received' if received else 'duplicate/noise'}")
    await engine.dispose()


asyncio.run(main())
