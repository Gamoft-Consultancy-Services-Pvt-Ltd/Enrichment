"""Unit tests for COMP-303 right-to-erasure — modules/lead_ingestion/erasure.py.

No DB, no network. Sessions are AsyncMocks; we assert against ORM mutations.
"""

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from modules.lead_ingestion.db.models import ErasureEventLog, Lead, LeadTouchpoint
from modules.lead_ingestion.erasure import erase_lead_by_identity


def _make_lead(
    *,
    tenant_id: uuid.UUID | None = None,
    phone: str | None = "+919876543210",
    email: str | None = "test@example.com",
) -> Lead:
    return Lead(
        id=uuid.uuid4(),
        tenant_id=tenant_id or uuid.uuid4(),
        channel_connection_id=None,
        pipeline_stage="captured",
        source_channel="file_upload",
        full_name="Test User",
        phone=phone,
        email=email,
        location="Mumbai",
        raw_event_json={"original": "event"},
        extra_fields={"budget": "50000"},
        pre_flight_block_reason=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _make_touchpoint(lead_id: uuid.UUID) -> LeadTouchpoint:
    return LeadTouchpoint(
        id=uuid.uuid4(),
        lead_id=lead_id,
        platform_event_id=None,
        source_channel="file_upload",
        raw_event_json={"original": "event"},
        created_at=datetime.now(UTC),
    )


def _session_returning(
    leads: list[Lead], touchpoints: list[LeadTouchpoint] | None = None
) -> AsyncMock:
    """Build a mock session that returns leads on first execute, touchpoints on second, empty on subsequent."""
    tp_list: list[LeadTouchpoint] = touchpoints or []
    call_count = 0

    async def fake_execute(stmt: Any) -> MagicMock:
        nonlocal call_count
        call_count += 1
        mock_result = MagicMock()
        if call_count == 1:
            mock_result.scalars.return_value.all.return_value = leads
        elif call_count == 2:
            mock_result.scalars.return_value.all.return_value = tp_list
        else:
            mock_result.scalars.return_value.all.return_value = []
        return mock_result

    session = AsyncMock()
    session.execute = fake_execute
    session.add = MagicMock()
    return session


# ---------------------------------------------------------------------------
# Empty match → ([], None), no audit row
# ---------------------------------------------------------------------------


async def test_erase_not_found_returns_empty() -> None:
    tenant_id = uuid.uuid4()
    session = _session_returning([])

    erased_ids, event = await erase_lead_by_identity(
        session, tenant_id, phone="+919876543210", requested_by="auth0|user1"
    )

    assert erased_ids == []
    assert event is None
    session.add.assert_not_called()


# ---------------------------------------------------------------------------
# Found lead → PII wiped, stage='erased', tombstone set
# ---------------------------------------------------------------------------


async def test_erase_nulls_pii_columns() -> None:
    tenant_id = uuid.uuid4()
    lead = _make_lead(tenant_id=tenant_id)
    session = _session_returning([lead])

    await erase_lead_by_identity(session, tenant_id, phone="+919876543210", requested_by="auth0|u")

    assert lead.full_name is None
    assert lead.phone is None
    assert lead.email is None
    assert lead.location is None
    assert lead.extra_fields is None


async def test_erase_sets_stage_erased() -> None:
    tenant_id = uuid.uuid4()
    lead = _make_lead(tenant_id=tenant_id)
    session = _session_returning([lead])

    await erase_lead_by_identity(session, tenant_id, phone="+919876543210", requested_by="auth0|u")

    assert lead.pipeline_stage == "erased"


async def test_erase_sets_erased_tombstone() -> None:
    tenant_id = uuid.uuid4()
    lead = _make_lead(tenant_id=tenant_id)
    session = _session_returning([lead])

    await erase_lead_by_identity(session, tenant_id, phone="+919876543210", requested_by="auth0|u")

    assert lead.raw_event_json.get("erased") is True
    assert "erased_at" in lead.raw_event_json


# ---------------------------------------------------------------------------
# Touchpoints redacted
# ---------------------------------------------------------------------------


async def test_erase_redacts_touchpoints() -> None:
    tenant_id = uuid.uuid4()
    lead = _make_lead(tenant_id=tenant_id)
    tp = _make_touchpoint(lead.id)
    session = _session_returning([lead], touchpoints=[tp])

    await erase_lead_by_identity(session, tenant_id, phone="+919876543210", requested_by="auth0|u")

    assert tp.raw_event_json.get("erased") is True


# ---------------------------------------------------------------------------
# ErasureEventLog written with correct fields
# ---------------------------------------------------------------------------


async def test_erase_writes_erasure_event_log() -> None:
    tenant_id = uuid.uuid4()
    lead = _make_lead(tenant_id=tenant_id)
    added: list[Any] = []
    session = _session_returning([lead])
    session.add = MagicMock(side_effect=added.append)

    await erase_lead_by_identity(
        session, tenant_id, phone="+919876543210", requested_by="auth0|user1"
    )

    logs = [obj for obj in added if isinstance(obj, ErasureEventLog)]
    assert len(logs) == 1
    log_row = logs[0]
    assert log_row.tenant_id == tenant_id
    assert log_row.identifier_type == "phone"
    assert str(lead.id) in log_row.erased_lead_ids
    assert log_row.requested_by == "auth0|user1"


async def test_erase_identifier_hash_is_sha256_not_value() -> None:
    tenant_id = uuid.uuid4()
    phone = "+919876543210"
    lead = _make_lead(tenant_id=tenant_id, phone=phone)
    added: list[Any] = []
    session = _session_returning([lead])
    session.add = MagicMock(side_effect=added.append)

    await erase_lead_by_identity(session, tenant_id, phone=phone, requested_by="auth0|u")

    logs = [obj for obj in added if isinstance(obj, ErasureEventLog)]
    assert len(logs) == 1
    expected_hash = hashlib.sha256(phone.encode()).hexdigest()
    assert logs[0].identifier_hash == expected_hash
    assert logs[0].identifier_hash != phone


# ---------------------------------------------------------------------------
# LeadErasureRequested event returned
# ---------------------------------------------------------------------------


async def test_erase_returns_erasure_event() -> None:
    tenant_id = uuid.uuid4()
    lead = _make_lead(tenant_id=tenant_id)
    session = _session_returning([lead])

    erased_ids, event = await erase_lead_by_identity(
        session, tenant_id, phone="+919876543210", requested_by="auth0|u"
    )

    assert event is not None
    assert lead.id in erased_ids
    assert event.identifier_type == "phone"
    assert event.erased_lead_ids == erased_ids
