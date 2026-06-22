"""Unit tests for normalise_lead_ad_form — no DB, no network."""

import uuid

from modules.lead_ingestion.normaliser import normalise_lead_ad_form
from shared.events.schemas import LeadSource

_TENANT = uuid.uuid4()
_CONN = uuid.uuid4()


def test_source_is_facebook_lead_ads() -> None:
    event = normalise_lead_ad_form([], leadgen_id="lg-1", tenant_id=_TENANT)
    assert event.source == LeadSource.FACEBOOK_LEAD_ADS


def test_platform_event_id_uses_leadgen_id() -> None:
    event = normalise_lead_ad_form([], leadgen_id="lg-abc123", tenant_id=_TENANT)
    assert event.platform_event_id == "leadgen-lg-abc123"


def test_standard_fields_mapped_correctly() -> None:
    field_data = [
        {"name": "full_name", "values": ["Priya Mehta"]},
        {"name": "email", "values": ["priya@example.com"]},
        {"name": "phone_number", "values": ["+91 98765 43210"]},
    ]
    event = normalise_lead_ad_form(field_data, leadgen_id="lg-1", tenant_id=_TENANT)
    assert event.full_name == "Priya Mehta"
    assert event.email == "priya@example.com"
    assert event.phone == "+91 98765 43210"


def test_email_is_lowercased() -> None:
    field_data = [{"name": "email", "values": ["UPPER@EXAMPLE.COM"]}]
    event = normalise_lead_ad_form(field_data, leadgen_id="lg-1", tenant_id=_TENANT)
    assert event.email == "upper@example.com"


def test_first_last_name_joined_when_full_name_absent() -> None:
    field_data = [
        {"name": "first_name", "values": ["Rahul"]},
        {"name": "last_name", "values": ["Sharma"]},
    ]
    event = normalise_lead_ad_form(field_data, leadgen_id="lg-1", tenant_id=_TENANT)
    assert event.full_name == "Rahul Sharma"


def test_full_name_preferred_over_first_last() -> None:
    field_data = [
        {"name": "full_name", "values": ["Full Name"]},
        {"name": "first_name", "values": ["First"]},
        {"name": "last_name", "values": ["Last"]},
    ]
    event = normalise_lead_ad_form(field_data, leadgen_id="lg-1", tenant_id=_TENANT)
    assert event.full_name == "Full Name"


def test_work_phone_used_as_fallback_when_phone_absent() -> None:
    field_data = [{"name": "work_phone_number", "values": ["+91 11111 22222"]}]
    event = normalise_lead_ad_form(field_data, leadgen_id="lg-1", tenant_id=_TENANT)
    assert event.phone == "+91 11111 22222"


def test_location_assembled_from_city_state_country() -> None:
    field_data = [
        {"name": "city", "values": ["Mumbai"]},
        {"name": "state", "values": ["Maharashtra"]},
        {"name": "country", "values": ["India"]},
    ]
    event = normalise_lead_ad_form(field_data, leadgen_id="lg-1", tenant_id=_TENANT)
    assert event.location is not None
    assert "Mumbai" in event.location
    assert "Maharashtra" in event.location
    assert "India" in event.location


def test_unknown_fields_go_to_extra_fields() -> None:
    field_data = [
        {"name": "email", "values": ["a@b.com"]},
        {"name": "company_name", "values": ["Acme Corp"]},
        {"name": "job_title", "values": ["Manager"]},
    ]
    event = normalise_lead_ad_form(field_data, leadgen_id="lg-1", tenant_id=_TENANT)
    assert event.extra_fields["company_name"] == "Acme Corp"
    assert event.extra_fields["job_title"] == "Manager"
    assert "email" not in event.extra_fields


def test_canonical_fields_not_duplicated_in_extra() -> None:
    field_data = [
        {"name": "full_name", "values": ["Alice"]},
        {"name": "email", "values": ["alice@x.com"]},
        {"name": "phone_number", "values": ["+1 555 0000"]},
        {"name": "city", "values": ["NYC"]},
    ]
    event = normalise_lead_ad_form(field_data, leadgen_id="lg-1", tenant_id=_TENANT)
    for key in ("full_name", "email", "phone_number", "city"):
        assert key not in event.extra_fields


def test_empty_values_not_mapped() -> None:
    field_data = [
        {"name": "full_name", "values": [""]},
        {"name": "email", "values": ["real@x.com"]},
    ]
    event = normalise_lead_ad_form(field_data, leadgen_id="lg-1", tenant_id=_TENANT)
    assert event.full_name is None
    assert event.email == "real@x.com"


def test_channel_connection_id_passed_through() -> None:
    event = normalise_lead_ad_form(
        [], leadgen_id="lg-1", tenant_id=_TENANT, channel_connection_id=_CONN
    )
    assert event.channel_connection_id == _CONN


def test_raw_event_json_defaults_to_leadgen_id_and_field_data() -> None:
    field_data = [{"name": "email", "values": ["x@y.com"]}]
    event = normalise_lead_ad_form(field_data, leadgen_id="lg-42", tenant_id=_TENANT)
    assert event.raw_event_json["leadgen_id"] == "lg-42"


def test_raw_event_json_can_be_overridden() -> None:
    custom_raw = {"source": "webhook", "leadgen_id": "lg-1", "page_id": "p-1"}
    event = normalise_lead_ad_form(
        [], leadgen_id="lg-1", tenant_id=_TENANT, raw_event_json=custom_raw
    )
    assert event.raw_event_json == custom_raw
