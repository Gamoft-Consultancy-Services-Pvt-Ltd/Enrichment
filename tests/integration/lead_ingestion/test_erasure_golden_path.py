"""Integration tests — COMP-303 erasure golden path."""

import hashlib
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from modules.lead_ingestion.db.models import ErasureEventLog, Lead, LeadTouchpoint
from modules.lead_ingestion.erasure import erase_lead_by_identity
from shared.tenant import service as tenant_service
from shared.tenant.schemas import BusinessType, TenantCreate


@pytest.fixture
async def tenant_id(session: AsyncSession) -> uuid.UUID:
    tenant = await tenant_service.create_tenant(
        session,
        TenantCreate(
            company_name="Erasure Test Co",
            primary_contact_name="Admin",
            primary_contact_email="admin@erasuretest.com",
            business_type=BusinessType.B2C,
            website_url="https://erasuretest.com",  # type: ignore[arg-type]
        ),
    )
    await session.commit()
    return tenant.id


async def _seed_lead(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    phone: str = "+919876543210",
    email: str = "user@example.com",
) -> Lead:
    lead = Lead(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        pipeline_stage="captured",
        source_channel="file_upload",
        full_name="Test User",
        phone=phone,
        email=email,
        location="Mumbai",
        raw_event_json={"original": "event"},
        extra_fields={"budget": "50000"},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.add(lead)
    await session.flush()
    return lead


async def _seed_touchpoint(session: AsyncSession, lead_id: uuid.UUID) -> LeadTouchpoint:
    tp = LeadTouchpoint(
        id=uuid.uuid4(),
        lead_id=lead_id,
        platform_event_id=None,
        source_channel="file_upload",
        raw_event_json={"original": "touchpoint_event"},
        created_at=datetime.now(UTC),
    )
    session.add(tp)
    await session.flush()
    return tp


async def test_erase_by_phone_nulls_pii(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    lead = await _seed_lead(session, tenant_id=tenant_id, phone="+919876543210")
    tp = await _seed_touchpoint(session, lead.id)
    lead_id, tp_id = lead.id, tp.id
    await session.commit()

    erased_ids, event = await erase_lead_by_identity(
        session, tenant_id, phone="+919876543210", requested_by="auth0|admin"
    )

    assert lead_id in erased_ids
    assert event is not None
    assert event.identifier_type == "phone"

    session.expire_all()
    refreshed = await session.get(Lead, lead_id)
    refreshed_tp = await session.get(LeadTouchpoint, tp_id)

    assert refreshed is not None
    assert refreshed.pipeline_stage == "erased"
    assert refreshed.full_name is None
    assert refreshed.phone is None
    assert refreshed.email is None
    assert refreshed.extra_fields is None
    assert refreshed.raw_event_json.get("erased") is True

    assert refreshed_tp is not None
    assert refreshed_tp.raw_event_json.get("erased") is True


async def test_erase_writes_erasure_event_log(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    await _seed_lead(session, tenant_id=tenant_id, phone="+919876543210")
    await session.commit()

    await erase_lead_by_identity(
        session, tenant_id, phone="+919876543210", requested_by="auth0|admin"
    )

    rows = (
        (
            await session.execute(
                select(ErasureEventLog).where(ErasureEventLog.tenant_id == tenant_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.identifier_type == "phone"
    assert row.identifier_hash == hashlib.sha256(b"+919876543210").hexdigest()
    assert row.requested_by == "auth0|admin"


async def test_erase_no_match_returns_empty(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    await session.commit()

    erased_ids, event = await erase_lead_by_identity(
        session, tenant_id, phone="+910000000000", requested_by="auth0|admin"
    )

    assert erased_ids == []
    assert event is None

    rows = (
        (
            await session.execute(
                select(ErasureEventLog).where(ErasureEventLog.tenant_id == tenant_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 0
