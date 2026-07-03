"""Unit tests for COMP-302 retention management — modules/lead_ingestion/retention.py.

No DB, no network. SQLAlchemy sessions are never awaited in a real connection here;
we pass MagicMock sessions and assert against the resulting ORM object mutations.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from modules.lead_ingestion.db.models import Lead, LeadTouchpoint, RetentionEventLog
from modules.lead_ingestion.retention import (
    anonymise_lead,
    get_leads_due_for_anonymisation,
)


def _make_lead(
    *,
    days_old: int,
    pipeline_stage: str = "captured",
    tenant_id: uuid.UUID | None = None,
) -> Lead:
    return Lead(
        id=uuid.uuid4(),
        tenant_id=tenant_id or uuid.uuid4(),
        channel_connection_id=None,
        pipeline_stage=pipeline_stage,
        source_channel="file_upload",
        full_name="Test User",
        phone="+919876543210",
        email="test@example.com",
        location="Mumbai",
        raw_event_json={"key": "value"},
        extra_fields={"budget": "50000"},
        pre_flight_block_reason=None,
        created_at=datetime.now(UTC) - timedelta(days=days_old),
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


# ---------------------------------------------------------------------------
# get_leads_due_for_anonymisation — policy engine
# ---------------------------------------------------------------------------


async def test_lead_731_days_old_returned() -> None:
    lead = _make_lead(days_old=731)
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [lead]
    session = AsyncMock()
    session.execute = AsyncMock(return_value=mock_result)

    results = await get_leads_due_for_anonymisation(session, retention_days=730)

    assert lead in results


async def test_lead_729_days_old_not_returned() -> None:
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    session = AsyncMock()
    session.execute = AsyncMock(return_value=mock_result)

    results = await get_leads_due_for_anonymisation(session, retention_days=730)

    assert results == []


async def test_already_anonymised_lead_excluded() -> None:
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    session = AsyncMock()
    session.execute = AsyncMock(return_value=mock_result)

    results = await get_leads_due_for_anonymisation(session, retention_days=730)

    assert results == []


# ---------------------------------------------------------------------------
# anonymise_lead — PII wipe + audit log
# ---------------------------------------------------------------------------


async def test_anonymise_lead_nulls_pii_columns() -> None:
    lead = _make_lead(days_old=731)
    session = AsyncMock()
    session.execute = AsyncMock(
        return_value=MagicMock(**{"scalars.return_value.all.return_value": []})
    )

    await anonymise_lead(session, lead, retention_days=730)

    assert lead.full_name is None
    assert lead.phone is None
    assert lead.email is None
    assert lead.location is None
    assert lead.extra_fields is None


async def test_anonymise_lead_sets_stage_anonymised() -> None:
    lead = _make_lead(days_old=731)
    session = AsyncMock()
    session.execute = AsyncMock(
        return_value=MagicMock(**{"scalars.return_value.all.return_value": []})
    )

    await anonymise_lead(session, lead, retention_days=730)

    assert lead.pipeline_stage == "anonymised"


async def test_anonymise_lead_raw_event_json_is_tombstone() -> None:
    lead = _make_lead(days_old=731)
    session = AsyncMock()
    session.execute = AsyncMock(
        return_value=MagicMock(**{"scalars.return_value.all.return_value": []})
    )

    await anonymise_lead(session, lead, retention_days=730)

    assert lead.raw_event_json.get("anonymised") is True
    assert "anonymised_at" in lead.raw_event_json


async def test_anonymise_lead_touchpoints_redacted() -> None:
    lead = _make_lead(days_old=731)
    tp = _make_touchpoint(lead.id)
    session = AsyncMock()
    # Return one touchpoint from the touchpoint query
    session.execute = AsyncMock(
        return_value=MagicMock(**{"scalars.return_value.all.return_value": [tp]})
    )

    await anonymise_lead(session, lead, retention_days=730)

    assert tp.raw_event_json.get("anonymised") is True


async def test_anonymise_lead_writes_retention_event_log() -> None:
    lead = _make_lead(days_old=731)
    session = AsyncMock()
    session.execute = AsyncMock(
        return_value=MagicMock(**{"scalars.return_value.all.return_value": []})
    )
    added: list[Any] = []
    session.add = MagicMock(side_effect=added.append)

    await anonymise_lead(session, lead, retention_days=730)

    logs = [obj for obj in added if isinstance(obj, RetentionEventLog)]
    assert len(logs) == 1
    log = logs[0]
    assert log.lead_id == lead.id
    assert log.tenant_id == lead.tenant_id
    assert log.action == "anonymised"
    assert log.retention_days_applied == 730
