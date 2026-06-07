"""Public schemas and enums for the tenant_config registry.

The single source of truth for the config status/dimension enums and the
validated value objects (signals, weights, thresholds). models.py, service.py,
modules/scoring, and tests import from here — never from models.py.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


class TenantConfigCreate(BaseModel):
    """The draft payload produced for a new version (manual now, agent later)."""

    business_profile: dict[str, Any]
    icp: dict[str, Any]
    signals: list[Signal]
    weights: Weights
    thresholds: Thresholds

    @field_validator("signals")
    @classmethod
    def _cover_all_dimensions_with_unique_ids(cls, value: list[Signal]) -> list[Signal]:
        if not value:
            raise ValueError("signals must not be empty")
        ids = [s.id for s in value]
        if len(ids) != len(set(ids)):
            raise ValueError("signal ids must be unique within a version")
        missing = set(Dimension) - {s.dimension for s in value}
        if missing:
            names = ", ".join(sorted(d.value for d in missing))
            raise ValueError(f"every dimension needs at least one signal; missing: {names}")
        return value


class TenantConfigRead(BaseModel):
    """The full config record returned to callers; built from the ORM object.

    The JSONB columns come back as plain dicts/lists; pydantic re-validates them
    into the typed value objects, so a hand-edited bad row is caught on read.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    version: int
    status: ConfigStatus
    business_profile: dict[str, Any]
    icp: dict[str, Any]
    signals: list[Signal]
    weights: Weights
    thresholds: Thresholds
    created_at: datetime
    activated_at: datetime | None
    archived_at: datetime | None
