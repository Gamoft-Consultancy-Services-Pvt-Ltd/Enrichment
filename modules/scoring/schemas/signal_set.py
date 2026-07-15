"""
SignalSet schemas

Pydantic v2 models defining the runtime scoring configuration:
weights, thresholds, machine-readable conditions, positive signals,
negative signals (incl. hard blocks), and the SignalSet container.

Design constraints :
- Five dimensions: Fit, Intent, Engagement, Context, Behaviour.
- Dimension weights are 0-100 point budgets summing to exactly 100
  (scoring_weights_final is the single source of truth upstream).
- Thresholds must satisfy hot_min > warm_min > cold_max.
- Positive Signal points must be > 0; NegativeSignal points must be < 0.
- `between` conditions require both value and value2.
- Models must round-trip cleanly to JSON / Postgres JSONB.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

WEIGHT_SUM_TOLERANCE = 0.01  

class Dimension(str, Enum):

    FIT = "fit"
    INTENT = "intent"
    ENGAGEMENT = "engagement"
    CONTEXT = "context"
    BEHAVIOUR = "behaviour"


class ConditionOperator(str, Enum):

    EQ = "eq"
    NEQ = "neq"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IN = "in"
    NOT_IN = "not_in"
    BETWEEN = "between"
    CONTAINS = "contains"
    NOT_CONTAINS = "not_contains"
    EXISTS = "exists"
    NOT_EXISTS = "not_exists"


_VALUE_FREE_OPERATORS = {ConditionOperator.EXISTS, ConditionOperator.NOT_EXISTS}
_LIST_OPERATORS = {ConditionOperator.IN, ConditionOperator.NOT_IN}


class ScoringWeights(BaseModel):
    """Per-dimension point budgets. Must sum to exactly 100 (± tolerance)."""

    model_config = ConfigDict(extra="forbid")

    fit: float = Field(..., ge=0, le=100)
    intent: float = Field(..., ge=0, le=100)
    engagement: float = Field(..., ge=0, le=100)
    context: float = Field(..., ge=0, le=100)
    behaviour: float = Field(..., ge=0, le=100)

    @model_validator(mode="after")
    def _validate_sum(self) -> "ScoringWeights":
        total = self.fit + self.intent + self.engagement + self.context + self.behaviour
        if abs(total - 100.0) > WEIGHT_SUM_TOLERANCE:
            raise ValueError(
                f"Scoring weights must sum to 100, got {total:g} "
                f"(fit={self.fit}, intent={self.intent}, engagement={self.engagement}, "
                f"context={self.context}, behaviour={self.behaviour})"
            )
        return self

    def budget_for(self, dimension: Dimension | str) -> float:
        """Return the 0-100 point budget for a dimension."""
        key = dimension.value if isinstance(dimension, Dimension) else str(dimension).lower()
        return float(getattr(self, key))

    def as_dict(self) -> dict[str, float]:
        return {d.value: self.budget_for(d) for d in Dimension}


class Thresholds(BaseModel):
    """Classification cut-offs. Invariant: hot_min > warm_min > cold_max."""

    model_config = ConfigDict(extra="forbid")

    hot_min: float = Field(..., ge=0, le=100)
    warm_min: float = Field(..., ge=0, le=100)
    cold_max: float = Field(..., ge=0, le=100)

    @model_validator(mode="after")
    def _validate_ordering(self) -> "Thresholds":
        if not (self.hot_min > self.warm_min > self.cold_max):
            raise ValueError(
                f"Thresholds must satisfy hot_min > warm_min > cold_max, "
                f"got hot_min={self.hot_min}, warm_min={self.warm_min}, "
                f"cold_max={self.cold_max}"
            )
        return self



class SignalCondition(BaseModel):
    """Machine-readable condition evaluated by SignalEvaluator (LEAD-49).

    `field` is a dot-path into LeadFeatures.fields (e.g. "company.employee_count").
    """

    model_config = ConfigDict(extra="forbid")

    field: str = Field(..., min_length=1)
    operator: ConditionOperator
    value: Optional[Any] = None
    value2: Optional[Any] = None  # upper bound, used only by `between`

    @model_validator(mode="after")
    def _validate_value_requirements(self) -> "SignalCondition":
        op = self.operator
        if op == ConditionOperator.BETWEEN:
            if self.value is None or self.value2 is None:
                raise ValueError("'between' operator requires both value and value2")
        elif op in _VALUE_FREE_OPERATORS:
            # exists / not_exists take no comparison value
            if self.value is not None or self.value2 is not None:
                raise ValueError(f"'{op.value}' operator must not carry value/value2")
        else:
            if self.value is None:
                raise ValueError(f"'{op.value}' operator requires a value")
            if self.value2 is not None:
                raise ValueError(f"'{op.value}' operator must not carry value2")
            if op in _LIST_OPERATORS and not isinstance(self.value, (list, tuple, set)):
                raise ValueError(f"'{op.value}' operator requires a list value")
        return self


class Signal(BaseModel):
    """A positive scoring signal contributing points toward one dimension."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    dimension: Dimension
    observation: str = Field(..., min_length=1, description="Human-readable rule")
    condition: Optional[SignalCondition] = None  # optional: signal_values path may cover it
    points: float = Field(..., gt=0, le=100)  # negatives must be NegativeSignal
    confidence_min: float = Field(
        default=0.0, ge=0.0, le=1.0,
        description="Minimum field confidence required for this signal to fire",
    )
    metadata: dict[str, Any] = Field(default_factory=dict)



class NegativeSignal(BaseModel):
    """A penalty signal. Soft (deduction) or hard_block (score=0, blocked)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    observation: str = Field(..., min_length=1)
    condition: Optional[SignalCondition] = None
    points: float = Field(..., lt=0, ge=-100)  # must be strictly negative
    hard_block: bool = False
    confidence_min: float = Field(default=0.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)



class SignalSet(BaseModel):

    model_config = ConfigDict(extra="forbid")

    tenant_id: Optional[str] = None
    version: int = Field(default=1, ge=1)
    weights: ScoringWeights
    thresholds: Thresholds
    signals: list[Signal] = Field(default_factory=list)
    negative_signals: list[NegativeSignal] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("signals")
    @classmethod
    def _validate_unique_signal_ids(cls, v: list[Signal]) -> list[Signal]:
        ids = [s.id for s in v]
        if len(ids) != len(set(ids)):
            dupes = sorted({i for i in ids if ids.count(i) > 1})
            raise ValueError(f"Duplicate signal ids: {dupes}")
        return v


    def signals_for_dimension(self, dimension: Dimension | str) -> list[Signal]:
        dim = Dimension(dimension) if not isinstance(dimension, Dimension) else dimension
        return [s for s in self.signals if s.dimension == dim]

    def get_signal(self, signal_id: str) -> Optional[Signal]:
        return next((s for s in self.signals if s.id == signal_id), None)

    @property
    def hard_blocks(self) -> list[NegativeSignal]:
        return [n for n in self.negative_signals if n.hard_block]

    @property
    def soft_negatives(self) -> list[NegativeSignal]:
        return [n for n in self.negative_signals if not n.hard_block]


    def to_jsonb(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @classmethod
    def from_jsonb(cls, payload: dict[str, Any]) -> "SignalSet":
        return cls.model_validate(payload)
