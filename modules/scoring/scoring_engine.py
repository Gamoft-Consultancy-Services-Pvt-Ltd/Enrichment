"""Pure deterministic scoring: judgements + weights + thresholds -> score, bucket.

No I/O and no LLM, so identical judgements plus an identical config version
always produce an identical score (ADR 0003).

Rules:
- A judgement below CONFIDENCE_FLOOR is downgraded to UNKNOWN.
- UNKNOWN signals are excluded from the denominator: a dimension scores
  satisfied / (satisfied + not_satisfied), not satisfied / total.
- A dimension where nothing could be judged is dropped entirely and the
  remaining weights are renormalized, so a lead is not punished for a
  dimension enrichment could not reach.
"""

from modules.scoring.scoring_schemas import (
    CONFIDENCE_FLOOR,
    DimensionScore,
    ScoringResult,
    SignalJudgment,
    Verdict,
)
from shared.events.schemas import LeadBucket
from shared.tenant_config.schemas import Dimension, TenantConfigRead


def compute_score(config: TenantConfigRead, judgments: list[SignalJudgment]) -> ScoringResult:
    """Turn per-signal judgements into a total score and bucket."""
    by_id = {j.signal_id: j for j in judgments}

    dimensions: dict[str, DimensionScore] = {}
    total_satisfied = total_judged = total_unknown = 0

    # Iterate the enum, not a set comprehension over signals: set order varies
    # between runs, and the trace must be byte-identical for identical inputs.
    for dimension in Dimension:
        signals = [s for s in config.signals if s.dimension == dimension]
        if not signals:
            continue
        key = dimension.value.lower()
        satisfied = judged = unknown = 0

        for signal in signals:
            verdict = _effective_verdict(by_id.get(signal.id))
            if verdict is Verdict.UNKNOWN:
                unknown += 1
                continue
            judged += 1
            if verdict is Verdict.SATISFIED:
                satisfied += 1

        dimensions[key] = DimensionScore(
            score=(satisfied / judged) if judged else None,
            weight=float(getattr(config.weights, key)),
            satisfied=satisfied,
            judged=judged,
            unknown=unknown,
        )
        total_satisfied += satisfied
        total_judged += judged
        total_unknown += unknown

    total_score = _weighted_total(dimensions)
    bucket = _bucket_for(total_score, config)

    return ScoringResult(
        config_version=config.version,
        total_score=total_score,
        bucket=bucket,
        coverage={
            "judged": total_judged,
            "unknown": total_unknown,
            "total": total_judged + total_unknown,
        },
        dimensions=dimensions,
        judgments=judgments,
    )


def _effective_verdict(judgment: SignalJudgment | None) -> Verdict:
    """A missing judgement, or one the LLM was not confident about, is UNKNOWN."""
    if judgment is None:
        return Verdict.UNKNOWN
    if judgment.confidence < CONFIDENCE_FLOOR:
        return Verdict.UNKNOWN
    return judgment.verdict


def _weighted_total(dimensions: dict[str, DimensionScore]) -> float | None:
    """Weighted mean over scorable dimensions only, renormalized to 0-100.

    Returns None when no dimension could be scored at all.
    """
    scorable = [d for d in dimensions.values() if d.score is not None]
    weight_sum = sum(d.weight for d in scorable)
    if not scorable or weight_sum == 0:
        return None
    earned = sum((d.score or 0.0) * d.weight for d in scorable)
    return round(earned / weight_sum * 100, 2)


def _bucket_for(score: float | None, config: TenantConfigRead) -> LeadBucket | None:
    if score is None:
        return None
    if score >= config.thresholds.hot:
        return LeadBucket.HOT
    if score >= config.thresholds.warm:
        return LeadBucket.WARM
    return LeadBucket.COLD
