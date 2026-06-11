"""Unit tests for lead_ingestion ORM models — introspection only, no DB required."""

from modules.lead_ingestion.db.models import (
    IntakeEventLog,
    Lead,
    LeadFormFieldMap,
    LeadTouchpoint,
)
from shared.channels.models import ChannelConnection

# ---------------------------------------------------------------------------
# Lead
# ---------------------------------------------------------------------------


def test_lead_table_name() -> None:
    assert Lead.__tablename__ == "leads"


def test_lead_nullable_identity_fields() -> None:
    cols = Lead.__table__.columns
    for col_name in ("full_name", "phone", "email", "location"):
        assert cols[col_name].nullable, f"Lead.{col_name} should be nullable"


def test_lead_non_nullable_required_fields() -> None:
    cols = Lead.__table__.columns
    for col_name in ("pipeline_stage", "source_channel", "raw_event_json"):
        assert not cols[col_name].nullable, f"Lead.{col_name} should be NOT NULL"


def test_lead_channel_connection_id_is_nullable_fk() -> None:
    col = Lead.__table__.columns["channel_connection_id"]
    assert col.nullable
    fk_targets = {fk.target_fullname for fk in col.foreign_keys}
    assert "channel_connections.id" in fk_targets


def test_lead_pre_flight_block_reason_nullable() -> None:
    assert Lead.__table__.columns["pre_flight_block_reason"].nullable


def test_lead_has_updated_at() -> None:
    assert "updated_at" in Lead.__table__.columns


# ---------------------------------------------------------------------------
# IntakeEventLog
# ---------------------------------------------------------------------------


def test_intake_event_log_table_name() -> None:
    assert IntakeEventLog.__tablename__ == "intake_event_logs"


def test_intake_event_log_platform_event_id_unique() -> None:
    table_args: tuple[object, ...] = getattr(IntakeEventLog, "__table_args__", ())
    constraint_names = {
        getattr(arg, "name", None) for arg in table_args if hasattr(arg, "columns")
    }
    assert "uq_intake_event_log_platform_event_id" in constraint_names


def test_intake_event_log_has_status() -> None:
    assert "status" in IntakeEventLog.__table__.columns
    assert not IntakeEventLog.__table__.columns["status"].nullable


def test_intake_event_log_lead_id_nullable() -> None:
    assert IntakeEventLog.__table__.columns["lead_id"].nullable


# ---------------------------------------------------------------------------
# LeadFormFieldMap
# ---------------------------------------------------------------------------


def test_lead_form_field_map_table_name() -> None:
    assert LeadFormFieldMap.__tablename__ == "lead_form_field_maps"


def test_lead_form_field_map_field_names() -> None:
    cols = LeadFormFieldMap.__table__.columns
    assert "source_field_name" in cols
    assert "canonical_field_name" in cols


# ---------------------------------------------------------------------------
# LeadTouchpoint
# ---------------------------------------------------------------------------


def test_lead_touchpoint_table_name() -> None:
    assert LeadTouchpoint.__tablename__ == "lead_touchpoints"


def test_lead_touchpoint_has_raw_event_json() -> None:
    assert "raw_event_json" in LeadTouchpoint.__table__.columns


# ---------------------------------------------------------------------------
# ChannelConnection (shared/channels)
# ---------------------------------------------------------------------------


def test_channel_connection_table_name() -> None:
    assert ChannelConnection.__tablename__ == "channel_connections"


def test_channel_connection_credentials_nullable() -> None:
    assert ChannelConnection.__table__.columns["credentials_encrypted"].nullable


def test_channel_connection_metadata_column_name() -> None:
    col = ChannelConnection.__table__.columns["metadata"]
    assert col is not None


def test_channel_connection_status_not_nullable() -> None:
    assert not ChannelConnection.__table__.columns["status"].nullable
