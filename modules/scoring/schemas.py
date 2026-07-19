"""Scoring data contracts: LLM judgements in, score + bucket out.

Pure data — no I/O, no LLM. `ScoringResult.to_trace()` produces the JSONB blob
persisted on leads.scoring.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from shared.events.schemas import LeadBucket

# Judgements below this confidence are downgraded to UNKNOWN (project
# calibration rule: a low-confidence guess should neither help nor hurt).
CONFIDENCE_FLOOR = 0.7


class Verdict(StrEnum):
    """The LLM's answer to one signal's question."""

    SATISFIED = "SATISFIED"
    NOT_SATISFIED = "NOT_SATISFIED"
    UNKNOWN = "UNKNOWN"


class SignalJudgment(BaseModel):
    """One signal, judged against the lead's enrichment + first-party data."""

    signal_id: str
    verdict: Verdict
    confidence: float = Field(ge=0, le=1)
    evidence: str = ""


class DimensionScore(BaseModel):
    """One dimension's outcome.

    `score` is satisfied/judged, or None if nothing in the dimension could be
    judged (that dimension is dropped from the total).
    """

    score: float | None
    weight: float
    satisfied: int
    judged: int
    unknown: int


class ScoringResult(BaseModel):
    """The full scoring outcome for one lead."""

    config_version: int
    total_score: float | None
    bucket: LeadBucket | None
    coverage: dict[str, int]
    dimensions: dict[str, DimensionScore]
    judgments: list[SignalJudgment]

    def to_trace(self) -> dict[str, Any]:
        """The JSONB blob written to leads.scoring."""
        trace: dict[str, Any] = self.model_dump(mode="json")
        trace["scored_at"] = datetime.now(UTC).isoformat()
        return trace
