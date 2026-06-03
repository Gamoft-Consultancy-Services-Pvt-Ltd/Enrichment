"""Unit tests for shared.events.schemas — pure validation, no DB."""

from datetime import UTC
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from shared.events.schemas import (
    Event,
    LeadBucket,
    LeadEnriched,
    LeadReceived,
    LeadSource,
    TenantActivated,
)


class _Sample(Event):
    """A concrete Event subclass used only to exercise the base envelope."""

    event_type: str = "Sample"


def test_event_autopopulates_id_and_timestamp() -> None:
    tenant_id = uuid4()
    evt = _Sample(tenant_id=tenant_id)
    assert isinstance(evt.event_id, UUID)
    assert evt.tenant_id == tenant_id
    assert evt.occurred_at.tzinfo is not None
    assert evt.occurred_at.tzinfo.utcoffset(evt.occurred_at) == UTC.utcoffset(None)


def test_event_instances_get_distinct_ids_and_timestamps() -> None:
    a = _Sample(tenant_id=uuid4())
    b = _Sample(tenant_id=uuid4())
    assert a.event_id != b.event_id


def test_event_requires_tenant_id() -> None:
    with pytest.raises(ValidationError):
        _Sample.model_validate({})


def test_event_is_frozen() -> None:
    evt = _Sample(tenant_id=uuid4())
    with pytest.raises(ValidationError):
        evt.tenant_id = uuid4()


def test_lead_source_membership_is_exact() -> None:
    assert {m.value for m in LeadSource} == {
        "GOOGLE_SHEETS",
        "EMAIL",
        "WHATSAPP",
        "INSTAGRAM",
    }


def test_lead_bucket_membership_is_exact() -> None:
    assert {m.value for m in LeadBucket} == {"HOT", "WARM", "COLD"}


def test_tenant_activated_carries_only_envelope() -> None:
    tenant_id = uuid4()
    evt = TenantActivated(tenant_id=tenant_id)
    assert evt.event_type == "TenantActivated"
    assert evt.tenant_id == tenant_id


def test_tenant_activated_is_frozen() -> None:
    evt = TenantActivated(tenant_id=uuid4())
    with pytest.raises(ValidationError):
        evt.tenant_id = uuid4()


def test_lead_received_carries_lead_id_and_source() -> None:
    tenant_id, lead_id = uuid4(), uuid4()
    evt = LeadReceived(tenant_id=tenant_id, lead_id=lead_id, source=LeadSource.EMAIL)
    assert evt.event_type == "LeadReceived"
    assert evt.lead_id == lead_id
    assert evt.source is LeadSource.EMAIL


def test_lead_received_requires_lead_id_and_source() -> None:
    with pytest.raises(ValidationError):
        LeadReceived.model_validate({"tenant_id": str(uuid4())})


def test_lead_received_rejects_invalid_source() -> None:
    with pytest.raises(ValidationError):
        LeadReceived.model_validate(
            {"tenant_id": str(uuid4()), "lead_id": str(uuid4()), "source": "CARRIER_PIGEON"}
        )


def test_lead_enriched_carries_lead_id() -> None:
    tenant_id, lead_id = uuid4(), uuid4()
    evt = LeadEnriched(tenant_id=tenant_id, lead_id=lead_id)
    assert evt.event_type == "LeadEnriched"
    assert evt.lead_id == lead_id


def test_lead_enriched_requires_lead_id() -> None:
    with pytest.raises(ValidationError):
        LeadEnriched.model_validate({"tenant_id": str(uuid4())})
