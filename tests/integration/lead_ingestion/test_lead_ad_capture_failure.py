"""Integration tests — PROD-002: run_lead_ad_capture writes audit log on Graph API failure.

Verifies that when fetch_lead_fields raises ExternalServiceError, the worker writes
an intake_event_logs row with status='failed' instead of silently discarding the event.
"""

import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import ExternalServiceError
from modules.lead_ingestion.db.models import IntakeEventLog
from shared.channels.models import ChannelConnection
from shared.tenant import service as tenant_service
from shared.tenant.schemas import BusinessType, TenantCreate
from workers.jobs.lead_ingestion import run_lead_ad_capture


async def _seed_tenant(session: AsyncSession) -> uuid.UUID:
    tenant = await tenant_service.create_tenant(
        session,
        TenantCreate(
            company_name=f"LeadAdFail Co {uuid.uuid4().hex[:6]}",
            primary_contact_name="Admin",
            primary_contact_email=f"admin_{uuid.uuid4().hex[:6]}@lafail.com",
            business_type=BusinessType.B2B,
            website_url="https://lafail.com",  # type: ignore[arg-type]
        ),
    )
    await session.commit()
    return tenant.id


async def _seed_facebook_connection(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    page_access_token: str,
) -> ChannelConnection:
    from core.config import get_settings
    from modules.lead_ingestion.crypto import encrypt_credentials

    encrypted = encrypt_credentials(
        {"page_access_token": page_access_token, "page_id": "pg-test"},
        key=get_settings().channel_credentials_encryption_key,
    )
    conn = ChannelConnection(
        tenant_id=tenant_id,
        channel_type="facebook",
        status="active",
        credentials_encrypted=encrypted,
        connection_metadata={"page_id": "pg-test"},
    )
    session.add(conn)
    await session.commit()
    return conn


async def test_lead_ad_fetch_failure_writes_failed_audit_log(
    session: AsyncSession,
) -> None:
    tenant_id = await _seed_tenant(session)
    conn = await _seed_facebook_connection(session, tenant_id, "tok-abc")
    leadgen_id = uuid.uuid4().hex

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from core.config import get_settings

    engine = create_async_engine(get_settings().database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    ctx: dict[str, Any] = {"session_factory": factory}
    payload: dict[str, Any] = {"object": "page", "entry": [{"id": "pg-test"}]}

    with patch(
        "workers.jobs.lead_ingestion.fetch_lead_fields",
        new=AsyncMock(side_effect=ExternalServiceError("Graph API 500")),
    ):
        await run_lead_ad_capture(
            ctx,
            {
                "tenant_id": str(tenant_id),
                "channel_connection_id": str(conn.id),
                "leadgen_id": leadgen_id,
                "raw_payload": payload,
            },
        )

    await engine.dispose()

    # Re-query through the test session to see committed rows
    logs = (
        (
            await session.execute(
                select(IntakeEventLog).where(
                    IntakeEventLog.platform_event_id == f"leadgen-{leadgen_id}"
                )
            )
        )
        .scalars()
        .all()
    )

    assert len(logs) == 1
    assert logs[0].status == "failed"
    assert logs[0].source_channel == "FACEBOOK_LEAD_ADS"
    assert logs[0].tenant_id == tenant_id


async def test_lead_ad_fetch_failure_is_idempotent(
    session: AsyncSession,
) -> None:
    """A retried job does not double-write the failed audit log."""
    tenant_id = await _seed_tenant(session)
    conn = await _seed_facebook_connection(session, tenant_id, "tok-xyz")
    leadgen_id = uuid.uuid4().hex

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from core.config import get_settings

    engine = create_async_engine(get_settings().database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ctx: dict[str, Any] = {"session_factory": factory}
    payload: dict[str, Any] = {}

    job_args = {
        "tenant_id": str(tenant_id),
        "channel_connection_id": str(conn.id),
        "leadgen_id": leadgen_id,
        "raw_payload": payload,
    }

    with patch(
        "workers.jobs.lead_ingestion.fetch_lead_fields",
        new=AsyncMock(side_effect=ExternalServiceError("Graph API 500")),
    ):
        await run_lead_ad_capture(ctx, job_args)
        await run_lead_ad_capture(ctx, job_args)  # retry

    await engine.dispose()

    logs = (
        (
            await session.execute(
                select(IntakeEventLog).where(
                    IntakeEventLog.platform_event_id == f"leadgen-{leadgen_id}"
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(logs) == 1  # ON CONFLICT DO NOTHING — not doubled
