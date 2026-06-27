"""LEAD-49-S12 + LEAD-51-S19 — evaluator and engine unit tests."""

import pytest

from engine import ScoringEngine
from evaluator import SignalEvaluator
from schemas.lead_features import LeadFeatures
from schemas.scoring_result import Classification, ConfidenceLevel
from schemas.signal_set import (
    ConditionOperator, Dimension, NegativeSignal, ScoringWeights, Signal,
    SignalCondition, SignalSet, Thresholds,
)

EV = SignalEvaluator()


def feats(fields=None, signal_values=None, confidence=None) -> LeadFeatures:
    return LeadFeatures(
        lead_id="L-1", tenant_id="t-1",
        fields=fields or {}, signal_values=signal_values or {},
        field_confidence=confidence or {},
    )


def cond(field, op, value=None, value2=None) -> SignalCondition:
    return SignalCondition(field=field, operator=op, value=value, value2=value2)


# ---------------------------------------------------------------------------
# SignalEvaluator — every operator + edge cases
# ---------------------------------------------------------------------------

class TestEvaluatorOperators:
    F = feats(fields={
        "industry": "saas",
        "employees": 120,
        "stack": ["mailchimp", "zendesk"],
        "note": "Compared us with CompetitorX",
        "company": {"country": "IN"},
    })

    @pytest.mark.parametrize("c,expected", [
        (cond("industry", ConditionOperator.EQ, "saas"), True),
        (cond("industry", ConditionOperator.EQ, "fintech"), False),
        (cond("industry", ConditionOperator.NEQ, "fintech"), True),
        (cond("employees", ConditionOperator.GT, 100), True),
        (cond("employees", ConditionOperator.GT, 120), False),
        (cond("employees", ConditionOperator.GTE, 120), True),
        (cond("employees", ConditionOperator.LT, 500), True),
        (cond("employees", ConditionOperator.LTE, 119), False),
        (cond("industry", ConditionOperator.IN, ["saas", "fintech"]), True),
        (cond("industry", ConditionOperator.NOT_IN, ["fintech"]), True),
        (cond("employees", ConditionOperator.BETWEEN, 50, 500), True),
        (cond("employees", ConditionOperator.BETWEEN, 120, 500), True),  # inclusive
        (cond("employees", ConditionOperator.BETWEEN, 200, 500), False),
        (cond("stack", ConditionOperator.CONTAINS, "mailchimp"), True),
        (cond("note", ConditionOperator.CONTAINS, "competitorx"), True),  # case-insensitive
        (cond("stack", ConditionOperator.NOT_CONTAINS, "hubspot"), True),
        (cond("company.country", ConditionOperator.EXISTS), True),
        (cond("company.city", ConditionOperator.NOT_EXISTS), True),
    ])
    def test_operators(self, c, expected):
        assert EV.evaluate(c, self.F) is expected

    def test_missing_field_false_except_existence(self):
        assert EV.evaluate(cond("ghost", ConditionOperator.GT, 1), self.F) is False
        assert EV.evaluate(cond("ghost", ConditionOperator.EQ, "x"), self.F) is False
        assert EV.evaluate(cond("ghost", ConditionOperator.NOT_EXISTS), self.F) is True
        assert EV.evaluate(cond("ghost", ConditionOperator.EXISTS), self.F) is False

    def test_type_mismatch_returns_false_not_raise(self):
        assert EV.evaluate(cond("industry", ConditionOperator.GT, 5), self.F) is False
        assert EV.evaluate(cond("employees", ConditionOperator.CONTAINS, "1"), self.F) is False
        # bool is rejected for numeric comparison
        f = feats(fields={"flag": True})
        assert EV.evaluate(cond("flag", ConditionOperator.GT, 0), f) is False


# ---------------------------------------------------------------------------
# ScoringEngine fixtures
# ---------------------------------------------------------------------------

def make_signal_set(**overrides) -> SignalSet:
    base = dict(
        tenant_id="t-1", version=1,
        weights=ScoringWeights(fit=30, intent=30, context=20, behaviour=15, engagement=5),
        thresholds=Thresholds(hot_min=75, warm_min=40, cold_max=39),
        signals=[
            Signal(id="fit_a", dimension=Dimension.FIT, observation="fit a", points=20,
                   condition=cond("a", ConditionOperator.EQ, True)),
            Signal(id="fit_b", dimension=Dimension.FIT, observation="fit b", points=20,
                   condition=cond("b", ConditionOperator.EQ, True)),  # 20+20 > 30 budget
            Signal(id="int_a", dimension=Dimension.INTENT, observation="int a", points=30,
                   condition=cond("c", ConditionOperator.GTE, 2)),
            Signal(id="ctx_a", dimension=Dimension.CONTEXT, observation="ctx a", points=20,
                   condition=cond("d", ConditionOperator.EQ, True)),
            Signal(id="beh_a", dimension=Dimension.BEHAVIOUR, observation="beh a", points=15,
                   condition=cond("e", ConditionOperator.EQ, True)),
            Signal(id="eng_a", dimension=Dimension.ENGAGEMENT, observation="eng a", points=5,
                   condition=cond("f", ConditionOperator.GTE, 3)),
            # no condition + no pre-computed value => always skipped
            Signal(id="int_skip", dimension=Dimension.INTENT, observation="precomputed only",
                   points=10),
        ],
        negative_signals=[
            NegativeSignal(id="neg_block", observation="competitor", points=-100,
                           hard_block=True,
                           condition=cond("is_competitor", ConditionOperator.EQ, True)),
            NegativeSignal(id="neg_soft", observation="unsubscribed", points=-10,
                           condition=cond("unsubscribed", ConditionOperator.EQ, True)),
        ],
    )
    base.update(overrides)
    return SignalSet(**base)


ENGINE = ScoringEngine()
FULL_FIRE_FIELDS = {"a": True, "b": True, "c": 5, "d": True, "e": True, "f": 4}


class TestScoringEngine:
    def test_hot_fixture_and_capping(self):
        f = feats(fields=FULL_FIRE_FIELDS,
                  signal_values={"intent": {"int_skip": False}})
        r = ENGINE.score(f, make_signal_set())
        fit = r.dimension_scores["fit"]
        assert fit.raw_points == 40 and fit.capped_points == 30  # cap at budget
        assert r.total_score == 100
        assert r.classification == Classification.HOT
        assert r.confidence == ConfidenceLevel.HIGH

    def test_warm_and_cold_fixtures(self):
        warm = feats(fields={"a": True, "b": False, "c": 5, "d": False, "e": False,
                             "f": 0},
                     signal_values={"intent": {"int_skip": False}})
        r = ENGINE.score(warm, make_signal_set())
        assert r.total_score == 50 and r.classification == Classification.WARM

        cold = feats(fields={k: False for k in FULL_FIRE_FIELDS},
                     signal_values={"intent": {"int_skip": False}})
        r = ENGINE.score(cold, make_signal_set())
        assert r.total_score == 0 and r.classification == Classification.COLD

    def test_hard_block(self):
        f = feats(fields={**FULL_FIRE_FIELDS, "is_competitor": True})
        r = ENGINE.score(f, make_signal_set())
        assert r.total_score == 0
        assert r.classification == Classification.BLOCKED
        assert r.blocked_by == "neg_block"

    def test_weak_confidence_hard_block_skipped(self):
        f = feats(fields={**FULL_FIRE_FIELDS, "is_competitor": True},
                  confidence={"is_competitor": 0.5},
                  signal_values={"intent": {"int_skip": False}})
        r = ENGINE.score(f, make_signal_set())
        assert r.classification == Classification.HOT  # block skipped at conf<0.6
        assert any("skipped" in n for n in r.scoring_notes)

    def test_soft_deduction_and_clamp(self):
        f = feats(fields={"a": True, "b": False, "c": 0, "d": False, "e": False,
                          "f": 0, "unsubscribed": True},
                  signal_values={"intent": {"int_skip": False}})
        r = ENGINE.score(f, make_signal_set())
        assert r.total_score == 10  # 20 fit - 10 soft
        assert r.soft_deductions[0].signal_id == "neg_soft"

        zero = feats(fields={k: False for k in FULL_FIRE_FIELDS} | {"unsubscribed": True},
                     signal_values={"intent": {"int_skip": False}})
        r = ENGINE.score(zero, make_signal_set())
        assert r.total_score == 0  # clamped, never negative

    def test_precomputed_signal_values_path_wins(self):
        # 'a' field says False but extractor pre-computed fired=True
        f = feats(fields={**FULL_FIRE_FIELDS, "a": False},
                  signal_values={"fit": {"fit_a": True},
                                 "intent": {"int_skip": False}})
        r = ENGINE.score(f, make_signal_set())
        assert "fit_a" in r.dimension_scores["fit"].fired_signal_ids

    def test_sparse_hot_prevention(self):
        # int_skip has no value/condition; remove pre-computed value for many
        # signals by making a set where most signals are unevaluable
        sparse_set = make_signal_set(signals=[
            Signal(id="int_a", dimension=Dimension.INTENT, observation="x", points=30,
                   condition=cond("c", ConditionOperator.GTE, 2)),
            Signal(id="s1", dimension=Dimension.FIT, observation="x", points=30),
            Signal(id="s2", dimension=Dimension.CONTEXT, observation="x", points=20),
            Signal(id="s3", dimension=Dimension.BEHAVIOUR, observation="x", points=15),
        ])
        # widen thresholds so the one fired signal reaches hot_min
        sparse_set = sparse_set.model_copy(update={
            "thresholds": Thresholds(hot_min=25, warm_min=10, cold_max=9)})
        f = feats(fields={"c": 5})
        r = ENGINE.score(f, sparse_set)
        assert r.confidence == ConfidenceLevel.LOW  # 1 evaluated / 3 skipped
        assert r.classification == Classification.WARM  # capped, not Hot
        assert any("sparse" in n for n in r.scoring_notes)

    def test_deterministic_repeatability(self):
        f = feats(fields=FULL_FIRE_FIELDS)
        r1 = ENGINE.score(f, make_signal_set())
        r2 = ENGINE.score(f, make_signal_set())
        assert r1.model_dump(exclude={"scored_at"}) == r2.model_dump(exclude={"scored_at"})

    def test_score_batch(self):
        batch = [feats(fields=FULL_FIRE_FIELDS), feats(fields={})]
        results = ENGINE.score_batch(batch, make_signal_set())
        assert len(results) == 2
