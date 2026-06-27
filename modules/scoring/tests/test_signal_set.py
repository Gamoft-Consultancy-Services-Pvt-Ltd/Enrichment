"""LEAD-44-S10 — Unit tests for SignalSet schemas."""

import pytest
from pydantic import ValidationError

from schemas.signal_set import (
    ConditionOperator,
    Dimension,
    NegativeSignal,
    ScoringWeights,
    Signal,
    SignalCondition,
    SignalSet,
    Thresholds,
)


def make_weights(**overrides) -> ScoringWeights:
    base = dict(fit=30, intent=25, engagement=20, context=15, behaviour=10)
    base.update(overrides)
    return ScoringWeights(**base)


def make_thresholds() -> Thresholds:
    return Thresholds(hot_min=75, warm_min=40, cold_max=39)


def make_signal(**overrides) -> Signal:
    base = dict(
        id="fit_industry_match",
        dimension=Dimension.FIT,
        observation="Lead industry matches ICP industry list",
        condition=SignalCondition(
            field="company.industry",
            operator=ConditionOperator.IN,
            value=["saas", "fintech"],
        ),
        points=10,
    )
    base.update(overrides)
    return Signal(**base)


# ScoringWeights


class TestScoringWeights:
    def test_valid_sum_100(self):
        w = make_weights()
        assert w.budget_for(Dimension.FIT) == 30
        assert w.budget_for("behaviour") == 10
        assert sum(w.as_dict().values()) == 100

    @pytest.mark.parametrize("fit", [29, 31])  # totals 99 and 101
    def test_invalid_sum_fails(self, fit):
        with pytest.raises(ValidationError, match="must sum to 100"):
            make_weights(fit=fit)

    def test_negative_weight_fails(self):
        with pytest.raises(ValidationError):
            make_weights(fit=-5, intent=60)



# Thresholds


class TestThresholds:
    def test_valid_ordering(self):
        t = make_thresholds()
        assert t.hot_min > t.warm_min > t.cold_max

    @pytest.mark.parametrize(
        "hot,warm,cold",
        [(40, 75, 39), (75, 75, 39), (75, 40, 40), (75, 40, 60)],
    )
    def test_invalid_ordering_fails(self, hot, warm, cold):
        with pytest.raises(ValidationError, match="hot_min > warm_min > cold_max"):
            Thresholds(hot_min=hot, warm_min=warm, cold_max=cold)

    def test_out_of_range_fails(self):
        with pytest.raises(ValidationError):
            Thresholds(hot_min=101, warm_min=40, cold_max=39)


# SignalCondition

class TestSignalCondition:
    def test_between_requires_both_values(self):
        ok = SignalCondition(
            field="company.employee_count",
            operator=ConditionOperator.BETWEEN,
            value=50, value2=500,
        )
        assert ok.value2 == 500
        with pytest.raises(ValidationError, match="between"):
            SignalCondition(
                field="company.employee_count",
                operator=ConditionOperator.BETWEEN,
                value=50,
            )

    def test_exists_takes_no_value(self):
        ok = SignalCondition(field="email", operator=ConditionOperator.EXISTS)
        assert ok.value is None
        with pytest.raises(ValidationError):
            SignalCondition(field="email", operator=ConditionOperator.EXISTS, value="x")

    def test_scalar_operator_requires_value(self):
        with pytest.raises(ValidationError, match="requires a value"):
            SignalCondition(field="score", operator=ConditionOperator.GT)

    def test_in_requires_list(self):
        with pytest.raises(ValidationError, match="list value"):
            SignalCondition(field="industry", operator=ConditionOperator.IN, value="saas")



# NegativeSignal

class TestSignals:
    def test_valid_positive_signal(self):
        s = make_signal()
        assert s.points == 10 and s.confidence_min == 0.0

    def test_negative_points_on_signal_fails(self):
        with pytest.raises(ValidationError):
            make_signal(points=-5)

    def test_zero_points_fails(self):
        with pytest.raises(ValidationError):
            make_signal(points=0)

    def test_condition_is_optional(self):
        s = make_signal(condition=None)
        assert s.condition is None  # pre-computed signal_values path

    def test_valid_negative_signal(self):
        n = NegativeSignal(
            id="neg_competitor",
            observation="Lead works at a direct competitor",
            points=-100,
            hard_block=True,
        )
        assert n.hard_block is True

    def test_positive_points_on_negative_signal_fails(self):
        with pytest.raises(ValidationError):
            NegativeSignal(id="neg_bad", observation="x", points=5)


# SignalSet container + serialization

class TestSignalSet:
    def make_set(self) -> SignalSet:
        return SignalSet(
            tenant_id="tenant-vd",
            version=3,
            weights=make_weights(),
            thresholds=make_thresholds(),
            signals=[
                make_signal(),
                make_signal(id="intent_demo_request", dimension=Dimension.INTENT,
                            observation="Requested a demo", points=15, condition=None),
            ],
            negative_signals=[
                NegativeSignal(id="neg_competitor", observation="Competitor employee",
                               points=-100, hard_block=True),
                NegativeSignal(id="neg_free_email", observation="Free email domain",
                               points=-5),
            ],
        )

    def test_valid_signal_set(self):
        ss = self.make_set()
        assert len(ss.signals_for_dimension(Dimension.FIT)) == 1
        assert ss.get_signal("intent_demo_request").points == 15
        assert [n.id for n in ss.hard_blocks] == ["neg_competitor"]
        assert [n.id for n in ss.soft_negatives] == ["neg_free_email"]

    def test_duplicate_signal_ids_fail(self):
        with pytest.raises(ValidationError, match="Duplicate signal ids"):
            SignalSet(
                weights=make_weights(),
                thresholds=make_thresholds(),
                signals=[make_signal(), make_signal()],
            )

    def test_jsonb_round_trip_lossless(self):
        ss = self.make_set()
        payload = ss.to_jsonb()
        import json
        json.dumps(payload)  # must be JSON-serializable for JSONB
        restored = SignalSet.from_jsonb(payload)
        assert restored == ss or restored.model_dump(mode="json") == payload

    def test_from_jsonb_revalidates(self):
        payload = self.make_set().to_jsonb()
        payload["weights"]["fit"] = 99  # break the 100-sum invariant
        with pytest.raises(ValidationError, match="must sum to 100"):
            SignalSet.from_jsonb(payload)
