"""
LEAD-50 — SignalSet adapter (Epic 6: Lead Scoring Runtime)

Pure transformation (no I/O) from the Epic 3 onboarding outputs into the
runtime SignalSet consumed by the ScoringEngine.

Inputs (Epic 3 contract — unchanged by this adapter):
- scoring_weights_final: {dimension: budget} summing to 100. Written by the
  Signal Agent; the single source of truth for dimension budgets.
- Signal[]: per-dimension signals whose `weight` values sum to 1.0 within
  each dimension. Optional machine-readable `condition`. Negative signals
  carry negative=True and optionally hard_block=True.

Mapping rule (LEAD-50-S4): signal points = dimension budget × signal weight.
e.g. budget 30, weight 0.5 -> 15 points.
"""

from __future__ import annotations

import math
from typing import Any

from schemas.signal_set import (
    Dimension,
    NegativeSignal,
    ScoringWeights,
    Signal,
    SignalCondition,
    SignalSet,
    Thresholds,
)

WEIGHT_SUM_TOLERANCE = 0.01


class AdapterError(ValueError):
    """Raised when Epic 3 inputs cannot be mapped to a valid SignalSet."""


def build_signal_set(
    epic3_signals: list[dict[str, Any]],
    scoring_weights_final: dict[str, float],
    thresholds: dict[str, float],
    *,
    tenant_id: str | None = None,
    version: int = 1,
) -> SignalSet:
    """Transform Epic 3 outputs into a validated runtime SignalSet."""

    # ---- LEAD-50-S3: budgets from scoring_weights_final -------------------
    try:
        weights = ScoringWeights(**{k.lower(): v for k, v in scoring_weights_final.items()})
    except Exception as exc:  # re-frame as adapter error with context
        raise AdapterError(f"scoring_weights_final invalid: {exc}") from exc

    thresholds_model = Thresholds(**thresholds)

    positives: list[Signal] = []
    negatives: list[NegativeSignal] = []
    per_dimension_weight_sums: dict[str, float] = {}

    for raw in epic3_signals:
        if raw.get("negative", False):
            negatives.append(_map_negative(raw))
            continue

        dim_key = str(raw["dimension"]).lower()
        dimension = Dimension(dim_key)
        weight = float(raw["weight"])
        if not (0 < weight <= 1):
            raise AdapterError(
                f"signal {raw.get('id')!r}: weight {weight} outside (0, 1]"
            )
        per_dimension_weight_sums[dim_key] = per_dimension_weight_sums.get(dim_key, 0.0) + weight

        budget = weights.budget_for(dimension)
        positives.append(Signal(
            id=raw["id"],
            dimension=dimension,
            observation=raw.get("observation", raw["id"]),
            condition=_map_condition(raw.get("condition")),  # LEAD-50-S5
            points=round(budget * weight, 4),                # LEAD-50-S4
            confidence_min=float(raw.get("confidence_min", 0.0)),
            metadata=raw.get("metadata", {}),
        ))

    # ---- per-dimension weights must sum to 1.0 (LEAD-50-S8 criteria) ------
    for dim_key, total in per_dimension_weight_sums.items():
        if not math.isclose(total, 1.0, abs_tol=WEIGHT_SUM_TOLERANCE):
            raise AdapterError(
                f"dimension {dim_key!r}: signal weights sum to {total:g}, expected 1.0"
            )

    # ---- LEAD-50-S7: full schema validation on the way out -----------------
    try:
        return SignalSet(
            tenant_id=tenant_id,
            version=version,
            weights=weights,
            thresholds=thresholds_model,
            signals=positives,
            negative_signals=negatives,
        )
    except Exception as exc:
        raise AdapterError(f"transformed SignalSet invalid: {exc}") from exc


# ---------------------------------------------------------------------------

def _map_condition(raw: dict[str, Any] | None) -> SignalCondition | None:
    if raw is None:
        return None
    return SignalCondition(**raw)


def _map_negative(raw: dict[str, Any]) -> NegativeSignal:  # LEAD-50-S6
    points = float(raw["points"])
    return NegativeSignal(
        id=raw["id"],
        observation=raw.get("observation", raw["id"]),
        condition=_map_condition(raw.get("condition")),
        points=points,
        hard_block=bool(raw.get("hard_block", False)),
        confidence_min=float(raw.get("confidence_min", 0.0)),
        metadata=raw.get("metadata", {}),
    )
