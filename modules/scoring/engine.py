"""
LEAD-51 — Deterministic ScoringEngine (Epic 6: Lead Scoring Runtime)

The primary scorer. Pure Python: identical inputs always produce identical
results. No LLM, no I/O, no randomness. The Sonnet Rating Agent (LEAD-52/53)
is a separate confidence-fallback layer on top of this engine — it can
estimate skipped signals, but it never replaces this engine and never
overrides a hard block.

Scoring algorithm:
  1. Hard blocks first. A hard-block negative signal that fires with field
     confidence >= 0.6 ends scoring: score 0, classification blocked.
     Below 0.6 the block is skipped (a lead is never blocked on weak data).
  2. Per dimension (Fit, Intent, Engagement, Context, Behaviour):
     for each signal, prefer the pre-computed signal_values outcome from
     Epic 5; fall back to condition evaluation; otherwise skip.
     Sum fired points, cap at the dimension budget.
  3. Apply soft negative deductions; clamp total to 0–100.
  4. Classify against tenant thresholds (hot/warm/cold).
  5. Derive confidence from the skipped-signal ratio. Sparse-data
     protection: low-confidence results can never classify Hot.
"""

from __future__ import annotations

from evaluator import SignalEvaluator
from schemas.lead_features import LeadFeatures
from schemas.scoring_result import (
    Classification,
    ConfidenceLevel,
    DimensionScore,
    ScoringResult,
    SoftDeduction,
)
from schemas.signal_set import Dimension, NegativeSignal, Signal, SignalSet

HARD_BLOCK_CONFIDENCE_MIN = 0.6  # LEAD-51-S4

# confidence from evaluated ratio (1 - skipped/total), LEAD-51-S16
CONFIDENCE_HIGH_MIN = 0.75
CONFIDENCE_MEDIUM_MIN = 0.50


class ScoringEngine:
    """Deterministic scorer. score() for one lead, score_batch() for many."""

    def __init__(self, evaluator: SignalEvaluator | None = None):
        self.evaluator = evaluator or SignalEvaluator()

    # ------------------------------------------------------------------

    def score(self, features: LeadFeatures, signal_set: SignalSet) -> ScoringResult:
        notes: list[str] = []

        # ---- 1. hard blocks (LEAD-51-S3 / S4) -----------------------------
        blocked_by = self._check_hard_blocks(features, signal_set, notes)
        if blocked_by is not None:
            return ScoringResult(
                lead_id=features.lead_id,
                tenant_id=features.tenant_id,
                signal_set_version=signal_set.version,
                total_score=0,
                classification=Classification.BLOCKED,
                blocked_by=blocked_by,
                confidence=ConfidenceLevel.HIGH,
                scoring_notes=notes,
            )

        # ---- 2. dimension scoring (LEAD-51-S5..S11) ------------------------
        dimension_scores: dict[str, DimensionScore] = {}
        evaluated = skipped_total = 0

        for dimension in Dimension:
            ds, n_eval, n_skip = self._score_dimension(
                dimension, signal_set, features
            )
            dimension_scores[dimension.value] = ds
            evaluated += n_eval
            skipped_total += n_skip

        subtotal = sum(ds.capped_points for ds in dimension_scores.values())

        # ---- 3. soft deductions + clamp (LEAD-51-S12 / S13) ----------------
        deductions = self._apply_soft_negatives(features, signal_set, notes)
        total = max(0.0, min(100.0, subtotal + sum(d.points for d in deductions)))

        # ---- 4 + 5. confidence, classification (LEAD-51-S14..S16) ----------
        confidence = self._derive_confidence(evaluated, skipped_total)
        classification = self._classify(total, signal_set, confidence, notes)

        return ScoringResult(
            lead_id=features.lead_id,
            tenant_id=features.tenant_id,
            signal_set_version=signal_set.version,
            total_score=round(total, 2),
            classification=classification,
            dimension_scores=dimension_scores,
            soft_deductions=deductions,
            confidence=confidence,
            scoring_notes=notes,
        )

    def score_batch(self, batch: list[LeadFeatures],
                    signal_set: SignalSet) -> list[ScoringResult]:
        """LEAD-51-S18: deterministic-only; by construction never calls an
        LLM — this engine has no LLM dependency at all. The agent layer
        (LEAD-53) must route batches here directly with fallback disabled."""
        return [self.score(f, signal_set) for f in batch]

    # ------------------------------------------------------------------

    def _check_hard_blocks(self, features: LeadFeatures, signal_set: SignalSet,
                           notes: list[str]) -> str | None:
        for neg in signal_set.hard_blocks:
            if not self._negative_fires(neg, features):
                continue
            conf = self._negative_confidence(neg, features)
            if conf < HARD_BLOCK_CONFIDENCE_MIN:  # LEAD-51-S4
                notes.append(
                    f"hard block {neg.id} matched but skipped "
                    f"(field confidence {conf:.2f} < {HARD_BLOCK_CONFIDENCE_MIN})"
                )
                continue
            notes.append(f"hard block fired: {neg.id} — {neg.observation}")
            return neg.id
        return None

    def _score_dimension(self, dimension: Dimension, signal_set: SignalSet,
                         features: LeadFeatures) -> tuple[DimensionScore, int, int]:
        budget = signal_set.weights.budget_for(dimension)
        fired: list[str] = []
        skipped: list[str] = []
        raw = 0.0

        for signal in signal_set.signals_for_dimension(dimension):
            outcome = self._evaluate_signal(signal, features)
            if outcome is None:
                skipped.append(signal.id)
            elif outcome:
                fired.append(signal.id)
                raw += signal.points

        capped = min(raw, budget)  # LEAD-51-S7..S11
        reasoning = (
            f"{len(fired)} fired / {len(skipped)} skipped; "
            f"{raw:g} raw pts capped at {budget:g} budget"
        )
        ds = DimensionScore(
            raw_points=raw, capped_points=capped, budget=budget,
            fired_signal_ids=fired, skipped_signal_ids=skipped,
            reasoning=reasoning,
        )
        n_total = len(signal_set.signals_for_dimension(dimension))
        return ds, n_total - len(skipped), len(skipped)

    def _evaluate_signal(self, signal: Signal,
                         features: LeadFeatures) -> bool | None:
        """True = fired, False = evaluated and did not fire, None = skipped."""
        # LEAD-51-S5: pre-computed signal_values path wins
        pre = features.get_signal_value(signal.dimension.value, signal.id)
        if pre is not None:
            return bool(pre)

        # LEAD-51-S6: condition fallback path
        if signal.condition is None:
            return None  # no value, no condition -> unevaluable, skipped

        if signal.confidence_min > 0:
            conf = features.get_field_confidence(signal.condition.field)
            if conf < signal.confidence_min:
                return None  # too uncertain to evaluate -> skipped

        return self.evaluator.evaluate(signal.condition, features)

    def _apply_soft_negatives(self, features: LeadFeatures, signal_set: SignalSet,
                              notes: list[str]) -> list[SoftDeduction]:
        deductions: list[SoftDeduction] = []
        for neg in signal_set.soft_negatives:
            if self._negative_fires(neg, features):
                deductions.append(SoftDeduction(
                    signal_id=neg.id, points=neg.points, observation=neg.observation,
                ))
        return deductions

    def _negative_fires(self, neg: NegativeSignal, features: LeadFeatures) -> bool:
        pre = features.get_signal_value("negative", neg.id)
        if pre is not None:
            return bool(pre)
        if neg.condition is None:
            return False
        return self.evaluator.evaluate(neg.condition, features)

    @staticmethod
    def _negative_confidence(neg: NegativeSignal, features: LeadFeatures) -> float:
        if neg.condition is not None:
            return features.get_field_confidence(neg.condition.field)
        return 1.0  # pre-computed by the extractor: trust its outcome

    @staticmethod
    def _derive_confidence(evaluated: int, skipped: int) -> ConfidenceLevel:
        total = evaluated + skipped
        if total == 0:
            return ConfidenceLevel.LOW
        ratio = evaluated / total
        if ratio >= CONFIDENCE_HIGH_MIN:
            return ConfidenceLevel.HIGH
        if ratio >= CONFIDENCE_MEDIUM_MIN:
            return ConfidenceLevel.MEDIUM
        return ConfidenceLevel.LOW

    @staticmethod
    def _classify(total: float, signal_set: SignalSet,
                  confidence: ConfidenceLevel, notes: list[str]) -> Classification:
        t = signal_set.thresholds
        if total >= t.hot_min:
            if confidence == ConfidenceLevel.LOW:  # LEAD-51-S15: sparse-Hot guard
                notes.append(
                    "sparse-data protection: score reached Hot threshold but "
                    "confidence is low — capped at Warm"
                )
                return Classification.WARM
            return Classification.HOT
        if total >= t.warm_min:
            return Classification.WARM
        return Classification.COLD
