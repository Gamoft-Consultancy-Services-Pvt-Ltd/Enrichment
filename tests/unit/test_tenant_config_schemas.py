"""Unit tests for shared.tenant_config.schemas — pure validation, no DB."""

import pytest
from pydantic import ValidationError

from shared.tenant_config.schemas import (
    ConfigStatus,
    Dimension,
    Signal,
    Thresholds,
    Weights,
)


def _balanced_weights() -> dict[str, float]:
    return {"fit": 0.2, "intent": 0.2, "engagement": 0.2, "behaviour": 0.2, "context": 0.2}


def test_config_status_membership_is_exact() -> None:
    assert {m.value for m in ConfigStatus} == {"DRAFT", "ACTIVE", "ARCHIVED", "REJECTED"}


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
