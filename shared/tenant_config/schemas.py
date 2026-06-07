"""Public schemas and enums for the tenant_config registry.

The single source of truth for the config status/dimension enums and the
validated value objects (signals, weights, thresholds). models.py, service.py,
modules/scoring, and tests import from here — never from models.py.
"""

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, Field, model_validator


class ConfigStatus(StrEnum):
    """The lifecycle state of a single tenant_config version."""

    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"
    REJECTED = "REJECTED"


class Dimension(StrEnum):
    """The five scoring dimensions every config must cover."""

    FIT = "FIT"
    INTENT = "INTENT"
    ENGAGEMENT = "ENGAGEMENT"
    BEHAVIOUR = "BEHAVIOUR"
    CONTEXT = "CONTEXT"


class Signal(BaseModel):
    """A single yes/no question tied to one scoring dimension."""

    id: str
    dimension: Dimension
    question: str


class Weights(BaseModel):
    """Per-dimension weights: all five present, each in [0, 1], summing to 1.0."""

    fit: float = Field(ge=0.0, le=1.0)
    intent: float = Field(ge=0.0, le=1.0)
    engagement: float = Field(ge=0.0, le=1.0)
    behaviour: float = Field(ge=0.0, le=1.0)
    context: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _sum_to_one(self) -> Self:
        total = self.fit + self.intent + self.engagement + self.behaviour + self.context
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"weights must sum to 1.0 (got {total})")
        return self


class Thresholds(BaseModel):
    """Score cutoffs for bucketing; hot must be strictly greater than warm."""

    hot: int = Field(ge=0, le=100)
    warm: int = Field(ge=0, le=100)

    @model_validator(mode="after")
    def _hot_above_warm(self) -> Self:
        if self.hot <= self.warm:
            raise ValueError(f"hot ({self.hot}) must be greater than warm ({self.warm})")
        return self
