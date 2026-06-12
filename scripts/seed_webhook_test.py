"""Seed script: create a test tenant + active config + WhatsApp ChannelConnection.

Run once before Swagger / webhook script testing:
    uv run python scripts/seed_webhook_test.py

The ChannelConnection is keyed to phone_number_id="987654321" — the same value
in tests/fixtures/lead_ingestion/wa_dm_01.json and wa_noise_01.json.

Prints the tenant_id and channel_connection_id on success.
"""

import asyncio
import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import modules.lead_ingestion.db.models  # noqa: F401
import shared.channels.models  # noqa: F401
import shared.tenant.models  # noqa: F401
import shared.tenant_config.models  # noqa: F401
from core.config import get_settings
from shared.channels.models import ChannelConnection
from shared.tenant import service as tenant_service
from shared.tenant.schemas import BusinessType, TenantCreate
from shared.tenant_config import service as config_service
from shared.tenant_config.schemas import TenantConfigCreate

_PHONE_NUMBER_ID = "987654321"  # must match the fixture files


async def main() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        # Create tenant
        tenant = await tenant_service.create_tenant(
            session,
            TenantCreate(
                company_name="Sprint3 Test Co",
                primary_contact_name="Priya Mehta",
                primary_contact_email="priya@sprint3test.com",
                business_type=BusinessType.B2C,
                website_url="https://sprint3test.com",  # type: ignore[arg-type]
            ),
        )

        # Create active tenant config (needed for pre-flight to pass)
        await config_service.create_active(
            session,
            tenant.id,
            TenantConfigCreate.model_validate(
                {
                    "business_profile": {"summary": "Real estate leads"},
                    "icp": {"summary": "Mid-market home buyers"},
                    "signals": [
                        {"id": "fit_1", "dimension": "FIT", "question": "In target city?"},
                        {"id": "intent_1", "dimension": "INTENT", "question": "Searching?"},
                        {"id": "eng_1", "dimension": "ENGAGEMENT", "question": "Replied?"},
                        {"id": "beh_1", "dimension": "BEHAVIOUR", "question": "Visited?"},
                        {"id": "ctx_1", "dimension": "CONTEXT", "question": "Life event?"},
                    ],
                    "weights": {
                        "fit": 0.2,
                        "intent": 0.2,
                        "engagement": 0.2,
                        "behaviour": 0.2,
                        "context": 0.2,
                    },
                    "thresholds": {"hot": 80, "warm": 55},
                }
            ),
        )

        # Create WhatsApp ChannelConnection
        conn = ChannelConnection(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            channel_type="whatsapp",
            status="active",
            connection_metadata={
                "phone_number_id": _PHONE_NUMBER_ID,
                "display_phone_number": "918001234567",
            },
        )
        session.add(conn)
        await session.commit()

        print(f"tenant_id            = {tenant.id}")
        print(f"channel_connection_id = {conn.id}")
        print(f"phone_number_id      = {_PHONE_NUMBER_ID}")
        print()
        print("Seed complete. Run scripts/send_test_webhook.py to fire a test message.")

    await engine.dispose()


asyncio.run(main())
