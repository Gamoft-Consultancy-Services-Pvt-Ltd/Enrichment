"""Cleanup Phase 20 fake tenant and ChannelConnection."""

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.config import get_settings

FAKE_TENANT_ID = "c487456b-c81e-4aad-b7f6-8b94209db30c"


async def main() -> None:
    s = get_settings()
    engine = create_async_engine(s.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        # Delete in FK dependency order: touchpoints/logs → leads → channel_connections → tenants
        await session.execute(
            text(
                "DELETE FROM lead_touchpoints WHERE lead_id IN (SELECT id FROM leads WHERE tenant_id = :tid)"
            ),
            {"tid": FAKE_TENANT_ID},
        )
        await session.execute(
            text("DELETE FROM intake_event_logs WHERE tenant_id = :tid"), {"tid": FAKE_TENANT_ID}
        )
        r2 = await session.execute(
            text("DELETE FROM leads WHERE tenant_id = :tid RETURNING id"), {"tid": FAKE_TENANT_ID}
        )
        n_leads = len(r2.fetchall())
        r1 = await session.execute(
            text(
                "DELETE FROM channel_connections WHERE metadata->>'phone_number_id' = 'TENANT2_PHONE_999' RETURNING id"
            )
        )
        n_conns = len(r1.fetchall())
        r3 = await session.execute(
            text("DELETE FROM tenants WHERE id = :tid RETURNING id"), {"tid": FAKE_TENANT_ID}
        )
        n_tenants = len(r3.fetchall())
        await session.commit()
        print(f"Deleted {n_conns} channel_connection(s)")
        print(f"Deleted {n_leads} lead(s)")
        print(f"Deleted {n_tenants} tenant(s)")
    await engine.dispose()


asyncio.run(main())
