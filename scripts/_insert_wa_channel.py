import asyncio
from sqlalchemy import text
from core.db import get_session

TENANT_ID = "5a4ba9f6-f1ff-47e2-8c11-856269d277b6"

async def main() -> None:
    async for session in get_session():
        result = await session.execute(text(
            "INSERT INTO channel_connections (id, tenant_id, channel_type, status, metadata, created_at, updated_at) "
            "VALUES (gen_random_uuid(), :tid, 'whatsapp', 'active', '{\"phone_number_id\": \"987654321\"}', now(), now()) "
            "RETURNING id"
        ), {"tid": TENANT_ID})
        await session.commit()
        row = result.fetchone()
        print("wa_connection_id:", row[0])

asyncio.run(main())
