"""Insert fake tenant + ChannelConnection for Phase 20 multi-tenant isolation test."""
import asyncio
import uuid
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy import text

import auth.models  # noqa: F401
import shared.tenant.models  # noqa: F401
import shared.tenant_config.models  # noqa: F401
import shared.channels.models  # noqa: F401
import modules.lead_ingestion.db.models  # noqa: F401

from core.config import get_settings


async def main() -> None:
    s = get_settings()
    engine = create_async_engine(s.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    fake_tenant_id = uuid.uuid4()
    conn_id = uuid.uuid4()

    insert_tenant = """
        INSERT INTO tenants (id, company_name, primary_contact_name, primary_contact_email,
                             business_type, website_url, timezone, language_preference,
                             status, onboarding_status, created_at, updated_at)
        VALUES (:tid, 'Fake Tenant Phase20', 'Test', 'phase20@fake.dev',
                'B2B', 'https://fake.example.com', 'UTC', 'en',
                'ACTIVE', 'COMPLETE', now(), now())
    """
    insert_conn = """
        INSERT INTO channel_connections (id, tenant_id, channel_type, status, metadata, created_at, updated_at)
        VALUES (:cid, :tid, 'whatsapp', 'active', '{"phone_number_id": "TENANT2_PHONE_999"}', now(), now())
        RETURNING id, tenant_id
    """
    async with factory() as session:
        await session.execute(text(insert_tenant), {"tid": fake_tenant_id})
        result = await session.execute(text(insert_conn), {"cid": conn_id, "tid": fake_tenant_id})
        await session.commit()
        row = result.fetchone()
        print(f"fake_tenant_id={fake_tenant_id}")
        print(f"channel_connection id={row[0]} tenant_id={row[1]}")
    await engine.dispose()


asyncio.run(main())
