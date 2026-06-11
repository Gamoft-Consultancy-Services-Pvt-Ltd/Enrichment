"""Unit tests for lead_ingestion Pydantic schemas — no DB, no network."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from modules.lead_ingestion.schemas.filter_result import FilterClassification, FilterResult
from modules.lead_ingestion.schemas.lead_form import STANDARD_FIELD_MAP, LeadFormFields
from modules.lead_ingestion.schemas.normalised_event import NormalisedChannelEvent
from shared.events.schemas import LeadSource

# ---------------------------------------------------------------------------
# NormalisedChannelEvent
# ---------------------------------------------------------------------------


def test_normalised_event_round_trip() -> None:
    evt = NormalisedChannelEvent(
        tenant_id=uuid4(),
        source=LeadSource.WHATSAPP,
        platform_event_id="wamid.abc123",
        full_name="Alice",
        phone="+919876543210",
        raw_event_json={"object": "whatsapp_business_account"},
    )
    assert evt.full_name == "Alice"
    assert evt.phone == "+919876543210"
    assert evt.email is None
    assert evt.extra_fields == {}
    assert evt.channel_connection_id is None


def test_normalised_event_is_frozen() -> None:
    evt = NormalisedChannelEvent(
        tenant_id=uuid4(),
        source=LeadSource.FILE_UPLOAD,
        platform_event_id="row-1",
        raw_event_json={},
    )
    with pytest.raises(ValidationError):
        evt.phone = "+910000000000"


def test_normalised_event_requires_tenant_source_platform_id() -> None:
    with pytest.raises(ValidationError):
        NormalisedChannelEvent.model_validate({"raw_event_json": {}})


def test_normalised_event_extra_fields_default_empty() -> None:
    evt = NormalisedChannelEvent(
        tenant_id=uuid4(),
        source=LeadSource.EMAIL,
        platform_event_id="msg-1",
        raw_event_json={},
    )
    assert evt.extra_fields == {}


def test_normalised_event_accepts_all_lead_sources() -> None:
    for source in LeadSource:
        evt = NormalisedChannelEvent(
            tenant_id=uuid4(),
            source=source,
            platform_event_id="x",
            raw_event_json={},
        )
        assert evt.source == source


# ---------------------------------------------------------------------------
# FilterResult
# ---------------------------------------------------------------------------


def test_filter_result_round_trip_lead() -> None:
    result = FilterResult(
        classification=FilterClassification.LEAD,
        extracted_fields={"name": "Bob", "phone": "+911234567890"},
        confidence=0.92,
    )
    assert result.classification is FilterClassification.LEAD
    assert result.extracted_fields["name"] == "Bob"
    assert result.confidence == 0.92


def test_filter_result_all_classifications_valid() -> None:
    for cls in FilterClassification:
        FilterResult(classification=cls)


def test_filter_result_defaults() -> None:
    result = FilterResult(classification=FilterClassification.NOISE)
    assert result.extracted_fields == {}
    assert result.confidence is None


def test_filter_result_rejects_unknown_classification() -> None:
    with pytest.raises(ValidationError):
        FilterResult.model_validate({"classification": "MAYBE"})


# ---------------------------------------------------------------------------
# LeadFormFields
# ---------------------------------------------------------------------------


def test_lead_form_fields_round_trip() -> None:
    fields = LeadFormFields(
        full_name="Charlie",
        phone="+910000000001",
        email="charlie@example.com",
        location="Mumbai",
        extra_fields={"Budget Range": "5-10L", "Notes": "Referred by Raj"},
    )
    assert fields.full_name == "Charlie"
    assert fields.extra_fields["Budget Range"] == "5-10L"


def test_lead_form_fields_all_optional() -> None:
    fields = LeadFormFields()
    assert fields.full_name is None
    assert fields.phone is None
    assert fields.email is None
    assert fields.location is None
    assert fields.extra_fields == {}


# ---------------------------------------------------------------------------
# STANDARD_FIELD_MAP
# ---------------------------------------------------------------------------


def test_standard_field_map_canonical_targets() -> None:
    allowed_targets = {"phone", "email", "full_name", "location"}
    for source, target in STANDARD_FIELD_MAP.items():
        assert target in allowed_targets, f"{source!r} maps to unexpected target {target!r}"


def test_standard_field_map_covers_common_aliases() -> None:
    assert STANDARD_FIELD_MAP["Mobile"] == "phone"
    assert STANDARD_FIELD_MAP["Email Address"] == "email"
    assert STANDARD_FIELD_MAP["Contact Name"] == "full_name"
