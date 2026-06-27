"""LEAD-53-S12 — Tests for LeadScoringAgent orchestration.

Verifies the control flow: engine-first, skip-on-high-confidence,
skip-on-blocked, fallback-on-low-confidence, recompute, llm_adjusted flag,
graceful degradation on failure, and fallback-off in batch.
"""

import json

import pytest

from agent import LeadScoringAgent
from engine import ScoringEngine
from rating_client import RatingClient
from schemas.lead_features import LeadFeatures
from schemas.scoring_result import Classification, ConfidenceLevel
from schemas.signal_set import (
    ConditionOperator, Dimension, NegativeSignal, ScoringWeights, Signal,
    SignalCondition, SignalSet, Thresholds,
)


# ---- fakes for the Sonnet client (reuse pattern from test_rating_client) ----

class _Block:
    def __init__(self, text): self.text = text


class _Resp:
    def __init__(self, text): self.content = [_Block(text)]


class FakeClient:
    def __init__(self, outputs):
        self._outputs = list(outputs)
        self.calls = 0
        self.messages = self

    def create(self, **kwargs):
        item = self._outputs[min(self.calls, len(self._outputs) - 1)]
        self.calls += 1
        if isinstance(item, Exception):
            raise item
        return _Resp(item)


def cond(field, op, value=None, value2=None):
    return SignalCondition(field=field, operator=op, value=value, value2=value2)


def make_signal_set() -> SignalSet:
    return SignalSet(
        tenant_id="t-1", version=1,
        weights=ScoringWeights(fit=30, intent=30, context=20, behaviour=15, engagement=5),
        thresholds=Thresholds(hot_min=40, warm_min=15, cold_max=14),
        signals=[
            Signal(id="fit_a", dimension=Dimension.FIT, observation="fit a", points=30,
                   condition=cond("a", ConditionOperator.EQ, True)),
            # these two have NO condition and NO precomputed value -> always skipped
            Signal(id="int_skip1", dimension=Dimension.INTENT,
                   observation="intent signal 1", points=30),
            Signal(id="ctx_skip1", dimension=Dimension.CONTEXT,
                   observation="context signal 1", points=20),
        ],
        negative_signals=[
            NegativeSignal(id="neg_block", observation="competitor", points=-100,
                           hard_block=True,
                           condition=cond("is_competitor", ConditionOperator.EQ, True)),
        ],
    )


def fallback_json(fire_ids, all_ids):
    return json.dumps({
        "estimates": [
            {"signal_id": sid, "would_fire": sid in fire_ids, "rationale": "x"}
            for sid in all_ids
        ],
        "overall_reasoning": "estimated from summary",
    })


def low_conf_lead() -> LeadFeatures:
    # only 'a' present -> fit_a fires, the two skip signals are skipped
    # 1 evaluated / 2 skipped => ratio 0.33 => LOW confidence
    return LeadFeatures(lead_id="L-1", tenant_id="t-1", fields={"a": True})


# ---------------------------------------------------------------------------

class TestNoFallbackCases:
    def test_high_confidence_skips_llm(self):
        # all signals evaluable -> HIGH confidence -> no LLM
        fake = FakeClient(["should not be called"])
        agent = LeadScoringAgent(rating_client=RatingClient(fake, sleep=lambda *_: None))
        ss = make_signal_set()
        # give precomputed values so nothing is skipped
        lead = LeadFeatures(lead_id="L-1", tenant_id="t-1", fields={"a": True},
                            signal_values={"intent": {"int_skip1": False},
                                           "context": {"ctx_skip1": False}})
        result = agent.score(lead, ss)
        assert result.confidence == ConfidenceLevel.HIGH
        assert result.llm_adjusted is False
        assert fake.calls == 0

    def test_blocked_skips_llm(self):
        fake = FakeClient(["should not be called"])
        agent = LeadScoringAgent(rating_client=RatingClient(fake, sleep=lambda *_: None))
        lead = LeadFeatures(lead_id="L-1", tenant_id="t-1",
                            fields={"a": True, "is_competitor": True},
                            field_confidence={"is_competitor": 0.95})
        result = agent.score(lead, make_signal_set())
        assert result.classification == Classification.BLOCKED
        assert result.llm_adjusted is False
        assert fake.calls == 0

    def test_no_client_means_no_fallback(self):
        agent = LeadScoringAgent(rating_client=None)
        result = agent.score(low_conf_lead(), make_signal_set())
        assert result.llm_adjusted is False
        assert result.confidence == ConfidenceLevel.LOW


class TestFallbackCases:
    def test_low_confidence_triggers_fallback_and_recomputes(self):
        # LLM says both skipped signals would fire -> they get added & re-scored
        payload = fallback_json(["int_skip1", "ctx_skip1"],
                                ["int_skip1", "ctx_skip1"])
        fake = FakeClient([payload])
        agent = LeadScoringAgent(rating_client=RatingClient(fake, sleep=lambda *_: None))
        ss = make_signal_set()

        before = ScoringEngine().score(low_conf_lead(), ss)
        after = agent.score(low_conf_lead(), ss)

        assert fake.calls == 1
        assert after.llm_adjusted is True
        # int_skip1 (30) + ctx_skip1 (20) now fire -> score strictly higher
        assert after.total_score > before.total_score
        assert any("LLM" in n for n in after.scoring_notes)

    def test_fallback_failure_returns_deterministic(self):
        fake = FakeClient([RuntimeError("down"), RuntimeError("down"),
                           RuntimeError("down")])
        agent = LeadScoringAgent(
            rating_client=RatingClient(fake, max_retries=2, sleep=lambda *_: None))
        ss = make_signal_set()

        deterministic = ScoringEngine().score(low_conf_lead(), ss)
        result = agent.score(low_conf_lead(), ss)

        assert result.llm_adjusted is False
        assert result.total_score == deterministic.total_score

    def test_llm_partial_estimate(self):
        # only one of the two skipped signals estimated to fire
        payload = fallback_json(["int_skip1"], ["int_skip1", "ctx_skip1"])
        fake = FakeClient([payload])
        agent = LeadScoringAgent(rating_client=RatingClient(fake, sleep=lambda *_: None))
        result = agent.score(low_conf_lead(), make_signal_set())
        assert result.llm_adjusted is True
        # fit_a (30) + int_skip1 (30) = 60
        assert result.total_score == 60


class TestBatch:
    def test_batch_forces_fallback_off(self):
        fake = FakeClient([fallback_json(["int_skip1"], ["int_skip1", "ctx_skip1"])])
        agent = LeadScoringAgent(rating_client=RatingClient(fake, sleep=lambda *_: None))
        results = agent.score_batch([low_conf_lead(), low_conf_lead()],
                                    make_signal_set())
        assert len(results) == 2
        assert all(r.llm_adjusted is False for r in results)
        assert fake.calls == 0  # no LLM calls in batch mode
