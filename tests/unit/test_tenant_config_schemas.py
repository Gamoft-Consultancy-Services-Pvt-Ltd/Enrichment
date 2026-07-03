"""Unit tests for shared.tenant_config.schemas — pure validation, no DB."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from shared.tenant_config.schemas import (
    ConfigStatus,
    Dimension,
    Signal,
    TenantConfigCreate,
    TenantConfigRead,
    Thresholds,
    Weights,
)


def _balanced_weights() -> dict[str, float]:
    return {"fit": 0.2, "intent": 0.2, "engagement": 0.2, "behaviour": 0.2, "context": 0.2}


def test_config_status_membership_is_exact() -> None:
    assert {m.value for m in ConfigStatus} == {"ACTIVE", "ARCHIVED"}


def test_dimension_membership_is_exact() -> None:
    assert {m.value for m in Dimension} == {
        "FIT",
        "INTENT",
        "ENGAGEMENT",
        "BEHAVIOUR",
        "CONTEXT",
    }


def test_weights_accepts_values_summing_to_one() -> None:
    w = Weights(**_balanced_weights())
    assert w.fit == 0.2


def test_weights_accepts_sum_within_float_tolerance() -> None:
    # 0.1 * 3 + 0.7 is not exactly 1.0 in float, but within tolerance.
    Weights(fit=0.1, intent=0.1, engagement=0.1, behaviour=0.0, context=0.7)


def test_weights_rejects_sum_not_one() -> None:
    bad = _balanced_weights() | {"context": 0.5}
    with pytest.raises(ValidationError):
        Weights(**bad)


def test_weights_rejects_out_of_range_value() -> None:
    bad = _balanced_weights() | {"fit": 1.5, "intent": -0.3}
    with pytest.raises(ValidationError):
        Weights(**bad)


def test_weights_rejects_missing_dimension() -> None:
    bad = _balanced_weights()
    del bad["context"]
    with pytest.raises(ValidationError):
        Weights(**bad)


def test_thresholds_accepts_hot_above_warm() -> None:
    t = Thresholds(hot=80, warm=55)
    assert t.hot == 80


def test_thresholds_rejects_hot_equal_to_warm() -> None:
    with pytest.raises(ValidationError):
        Thresholds(hot=55, warm=55)


def test_thresholds_rejects_hot_below_warm() -> None:
    with pytest.raises(ValidationError):
        Thresholds(hot=40, warm=55)


def test_thresholds_rejects_out_of_range() -> None:
    with pytest.raises(ValidationError):
        Thresholds(hot=120, warm=55)


def test_signal_requires_valid_dimension() -> None:
    with pytest.raises(ValidationError):
        Signal.model_validate({"id": "s1", "dimension": "NOPE", "question": "?"})


def _all_dimension_signals() -> list[dict[str, str]]:
    return [
        {"id": "fit_1", "dimension": "FIT", "question": "In target industry?"},
        {"id": "intent_1", "dimension": "INTENT", "question": "Visited pricing?"},
        {"id": "eng_1", "dimension": "ENGAGEMENT", "question": "Opened last email?"},
        {"id": "beh_1", "dimension": "BEHAVIOUR", "question": "Requested a demo?"},
        {"id": "ctx_1", "dimension": "CONTEXT", "question": "Raised funding recently?"},
    ]


def _valid_create_payload() -> dict[str, object]:
    return {
        "business_profile": {"summary": "B2B SaaS"},
        "icp": {"summary": "Mid-market SaaS in APAC"},
        "signals": _all_dimension_signals(),
        "weights": _balanced_weights(),
        "thresholds": {"hot": 80, "warm": 55},
    }


def test_tenant_config_create_accepts_valid_payload() -> None:
    data = TenantConfigCreate.model_validate(_valid_create_payload())
    assert len(data.signals) == 5
    assert data.weights.fit == 0.2


def test_tenant_config_create_rejects_empty_signals() -> None:
    payload = _valid_create_payload() | {"signals": []}
    with pytest.raises(ValidationError):
        TenantConfigCreate.model_validate(payload)


def test_tenant_config_create_rejects_duplicate_signal_ids() -> None:
    signals = _all_dimension_signals()
    signals[1]["id"] = signals[0]["id"]  # duplicate id
    payload = _valid_create_payload() | {"signals": signals}
    with pytest.raises(ValidationError):
        TenantConfigCreate.model_validate(payload)


def test_tenant_config_create_rejects_missing_dimension() -> None:
    signals = _all_dimension_signals()[:-1]  # drop the CONTEXT signal
    payload = _valid_create_payload() | {"signals": signals}
    with pytest.raises(ValidationError):
        TenantConfigCreate.model_validate(payload)


def test_tenant_config_read_builds_from_orm_like_object() -> None:
    obj = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        version=1,
        status=ConfigStatus.ACTIVE,
        business_profile={"summary": "B2B SaaS"},
        icp={"summary": "Mid-market SaaS"},
        signals=_all_dimension_signals(),
        weights=_balanced_weights(),
        thresholds={"hot": 80, "warm": 55},
        created_at=datetime.now(UTC),
        activated_at=datetime.now(UTC),
        archived_at=None,
    )
    read = TenantConfigRead.model_validate(obj)
    assert read.version == 1
    assert read.status is ConfigStatus.ACTIVE
    # nested JSONB payloads are coerced back into typed value objects on read
    assert read.weights.fit == 0.2
    assert read.signals[0].dimension.value == "FIT"
    assert read.archived_at is None
