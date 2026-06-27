"""
LEAD-46 — ScoringResult schemas (Epic 6: Lead Scoring Runtime)

Structured, validated output of the scoring engine. Every score carries
its classification, per-dimension breakdown, provenance, and an
explainable reasoning trace.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ---------------------------------------------------------------------------
# LEAD-46-S2 — Classification / confidence enums
# ---------------------------------------------------------------------------

class Classification(str, Enum):
    HOT = "hot"
    WARM = "warm"
    COLD = "cold"
    BLOCKED = "blocked"


class ConfidenceLevel(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# ---------------------------------------------------------------------------
# LEAD-46-S1 — DimensionScore
# ---------------------------------------------------------------------------

class DimensionScore(BaseModel):
    """Score breakdown for one of the five dimensions."""

    model_config = ConfigDict(extra="forbid")

    raw_points: float = Field(..., ge=0)
    capped_points: float = Field(..., ge=0)
    budget: float = Field(..., ge=0, le=100)
    fired_signal_ids: list[str] = Field(default_factory=list)
    skipped_signal_ids: list[str] = Field(default_factory=list)
    reasoning: str = ""

    @model_validator(mode="after")
    def _capped_within_budget(self) -> "DimensionScore":
        if self.capped_points > self.budget + 1e-9:
            raise ValueError(
                f"capped_points ({self.capped_points}) exceeds budget ({self.budget})"
            )
        return self


# ---------------------------------------------------------------------------
# LEAD-46-S3/S4/S5 — ScoringResult
# ---------------------------------------------------------------------------

class SoftDeduction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signal_id: str
    points: float = Field(..., lt=0)
    observation: str = ""


class ScoringResult(BaseModel):
    """The complete, auditable outcome of scoring one lead."""

    model_config = ConfigDict(extra="forbid")

    lead_id: str
    tenant_id: str
    signal_set_version: int = 1

    total_score: float = Field(..., ge=0, le=100)  # LEAD-46-S4
    classification: Classification
    dimension_scores: dict[str, DimensionScore] = Field(default_factory=dict)
    soft_deductions: list[SoftDeduction] = Field(default_factory=list)
    blocked_by: Optional[str] = None  # signal id of the hard block that fired

    confidence: ConfidenceLevel = ConfidenceLevel.HIGH
    llm_adjusted: bool = False
    scoring_notes: list[str] = Field(default_factory=list)
    scored_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # ---- LEAD-46-S5: blocked results must carry provenance ----------------

    @model_validator(mode="after")
    def _blocked_requires_blocked_by(self) -> "ScoringResult":
        if self.classification == Classification.BLOCKED and not self.blocked_by:
            raise ValueError("blocked classification requires blocked_by signal id")
        if self.classification != Classification.BLOCKED and self.blocked_by:
            raise ValueError("blocked_by may only be set on blocked results")
        return self

    # ---- convenience ------------------------------------------------------

    def explain(self) -> str:
        """Human-readable trace (used by the demo and day-end reports)."""
        lines = [
            f"Lead {self.lead_id} (tenant {self.tenant_id}) — "
            f"score {self.total_score:g}/100 → {self.classification.value.upper()} "
            f"[confidence: {self.confidence.value}]"
        ]
        if self.blocked_by:
            lines.append(f"  HARD BLOCK: {self.blocked_by}")
        for dim, ds in self.dimension_scores.items():
            lines.append(
                f"  {dim:<11} {ds.capped_points:>5.1f}/{ds.budget:<5.1f} "
                f"fired={ds.fired_signal_ids or '—'} skipped={ds.skipped_signal_ids or '—'}"
            )
        for d in self.soft_deductions:
            lines.append(f"  deduction   {d.points:>+6.1f}  {d.signal_id}: {d.observation}")
        for note in self.scoring_notes:
            lines.append(f"  note: {note}")
        return "\n".join(lines)
