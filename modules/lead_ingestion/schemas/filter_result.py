"""FilterResult — output of the two-stage noise filter (Sprint 3).

The classification enum drives which pipeline branch handles the event.
Calibration rule: UNCLEAR must be treated as LEAD (a missed lead costs more
than a processed non-lead).
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class FilterClassification(StrEnum):
    LEAD = "LEAD"
    NOISE = "NOISE"
    UNCLEAR = "UNCLEAR"
    EXISTING_CUSTOMER = "EXISTING_CUSTOMER"


class FilterResult(BaseModel):
    """Result produced by two_stage_filter for one inbound message."""

    classification: FilterClassification
    extracted_fields: dict[str, Any] = Field(default_factory=dict)
    confidence: float | None = None
