"""Integration tests for COMP-302 retention sweep — modules/lead_ingestion/retention.py."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from modules.lead_ingestion.db.models import Lead, LeadTouchpoint, RetentionEventLog
from modules.lead_ingestion.retention import run_retention_sweep
from shared.tenant import service as tenant_service
from shared.tenant.schemas import BusinessType, TenantCreate


def _past(days: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


async def _seed_lead(
    session: AsyncSession, *, tenant_id: uuid.UUID, days_old: int, pipeline_stage: str = "captured"
) -> Lead:
    uniq = uuid.uuid4().hex[:8]
    lead = Lead(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        pipeline_stage=pipeline_stage,
        source_channel="file_upload",
        full_name="Test User",
        phone=f"+9198765{uniq[:5]}",
        email=f"test_{uniq}@example.com",
        location="Mumbai",
        raw_event_json={"original": "event"},
        extra_fields={"budget": "50000"},
        created_at=_past(days_old),
        updated_at=_past(days_old),
    )
    session.add(lead)
    await session.flush()
    return lead


@pytest.fixture
async def tenant_id(session: AsyncSession) -> uuid.UUID:
    tenant = await tenant_service.create_tenant(
        session,
        TenantCreate(
            company_name="Retention Test Co",
            primary_contact_name="Admin",
            primary_contact_email="admin@rettest.com",
            business_type=BusinessType.B2C,
            website_url="https://rettest.com",  # type: ignore[arg-type]
            pan="AAACX1234C",
            pan_holder_name="Test Holder Pvt Ltd",
            pan_dob="01/04/2019",
            consent=True,
        ),
    )
    await session.commit()
    return tenant.id


async def test_sweep_anonymises_overdue_leads(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    lead1 = await _seed_lead(session, tenant_id=tenant_id, days_old=731)
    lead2 = await _seed_lead(session, tenant_id=tenant_id, days_old=800)
    lead3 = await _seed_lead(session, tenant_id=tenant_id, days_old=100)
    tp = LeadTouchpoint(
        id=uuid.uuid4(),
        lead_id=lead1.id,
        platform_event_id=None,
        source_channel="file_upload",
        raw_event_json={"original": "event"},
        created_at=datetime.now(UTC),
    )
    session.add(tp)
    await session.commit()

    # Capture PKs before expire_all to avoid async lazy-load error
    lead1_id, lead2_id, lead3_id, tp_id = lead1.id, lead2.id, lead3.id, tp.id

    result = await run_retention_sweep(session)

    assert result["leads_anonymised"] == 2

    # Expire identity map so get() issues real SELECTs against the DB
    session.expire_all()
    refreshed1 = await session.get(Lead, lead1_id)
    refreshed2 = await session.get(Lead, lead2_id)
    refreshed3 = await session.get(Lead, lead3_id)
    refreshed_tp = await session.get(LeadTouchpoint, tp_id)

    assert refreshed1 is not None
    assert refreshed1.pipeline_stage == "anonymised"
    assert refreshed1.full_name is None
    assert refreshed1.phone is None
    assert refreshed1.email is None
    assert refreshed1.extra_fields is None
    assert refreshed1.raw_event_json.get("anonymised") is True

    assert refreshed2 is not None
    assert refreshed2.pipeline_stage == "anonymised"

    # Recent lead untouched
    assert refreshed3 is not None
    assert refreshed3.pipeline_stage == "captured"
    assert refreshed3.full_name == "Test User"

    # Touchpoint redacted in DB (real SQL coverage for COMP-302 spec line 222)
    assert refreshed_tp is not None
    assert refreshed_tp.raw_event_json.get("anonymised") is True


async def test_sweep_writes_retention_event_log_rows(
    session: AsyncSession, tenant_id: uuid.UUID
) -> None:
    await _seed_lead(session, tenant_id=tenant_id, days_old=731)
    await _seed_lead(session, tenant_id=tenant_id, days_old=750)
    await session.commit()

    await run_retention_sweep(session)

    logs = (
        (
            await session.execute(
                select(RetentionEventLog).where(RetentionEventLog.tenant_id == tenant_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(logs) == 2
    for log in logs:
        assert log.action == "anonymised"
        assert log.retention_days_applied == 730


async def test_sweep_skips_already_anonymised(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    await _seed_lead(session, tenant_id=tenant_id, days_old=731, pipeline_stage="anonymised")
    await session.commit()

    result = await run_retention_sweep(session)

    assert result["leads_anonymised"] == 0

    logs = (
        (
            await session.execute(
                select(RetentionEventLog).where(RetentionEventLog.tenant_id == tenant_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(logs) == 0
