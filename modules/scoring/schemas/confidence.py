"""
COMP-1203-ST4 — Capture confidence scores (Epic 6, Domain 12: Fact Traceability)

The scoring-owned slice of Fact Traceability. The rest of COMP-1203 (source
URLs, retrieval timestamps, agent-run references, the fact audit trail) belongs
to enrichment / onboarding (Epic 3/5). Scoring owns *confidence*: the result
confidence the engine derives must be captured as a first-class, traceable,
queryable field — not just a bare float buried in the breakdown.

What "traceable" means here, beyond the number itself:
- the VALUE (0-1),
- the METHOD that produced it (so an auditor knows how it was derived),
- the INPUTS to that method (the skipped-signal counts your engine's
  _derive_confidence uses), so the value can be recomputed and defended,
- a coarse LEVEL (high/medium/low) for reporting and for the fallback gate.

This is a distinct model from ScoringProvenance (COMP-1201) on purpose:
traceability and provenance are separate compliance concerns and separate
stories. Provenance *references* the confidence value; this model is the
auditable record of how that value came to be. It is stored alongside
provenance in the breakdown JSONB (no schema change).
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, model_validator


class ConfidenceLevel(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ConfidenceMethod(str, Enum):
    """How the confidence value was derived."""

    # the engine's primary method: ratio of skipped to total signals
    SKIPPED_SIGNAL_RATIO = "skipped_signal_ratio"
    # confidence asserted directly (e.g. a blocked lead short-circuits scoring)
    SHORT_CIRCUIT = "short_circuit"


# Level thresholds on the 0-1 value. >>> CONFIRM against your engine's actual
# _derive_confidence cutoffs; these mirror the common high>=0.8 / low<0.5 split.
HIGH_MIN = 0.8
LOW_MAX = 0.5


class ConfidenceCapture(BaseModel):
    """ST4 — the auditable record of a score's confidence."""

    value: float = Field(..., ge=0.0, le=1.0, description="Derived confidence (0-1)")
    level: ConfidenceLevel = Field(..., description="Coarse band for reporting/gating")
    method: ConfidenceMethod = Field(..., description="How the value was derived")

    # inputs to the derivation, so the value is recomputable and defensible
    total_signals: int | None = Field(
        default=None, ge=0, description="Signals considered for the lead"
    )
    skipped_signals: int | None = Field(
        default=None, ge=0, description="Signals the engine could not evaluate"
    )
    triggered_fallback: bool = Field(
        default=False, description="Whether this confidence triggered the LLM fallback"
    )

    @model_validator(mode="after")
    def _check_signal_counts(self) -> "ConfidenceCapture":
        if (
            self.total_signals is not None
            and self.skipped_signals is not None
            and self.skipped_signals > self.total_signals
        ):
            raise ValueError("skipped_signals cannot exceed total_signals")
        return self

    @property
    def skipped_ratio(self) -> float | None:
        """The ratio the engine uses; None if counts weren't captured."""
        if not self.total_signals:
            return None
        return (self.skipped_signals or 0) / self.total_signals

    # ----------------------------------------------------------------- #
    @classmethod
    def from_value(
        cls,
        value: float,
        *,
        method: ConfidenceMethod = ConfidenceMethod.SKIPPED_SIGNAL_RATIO,
        total_signals: int | None = None,
        skipped_signals: int | None = None,
        triggered_fallback: bool = False,
    ) -> "ConfidenceCapture":
        """Build a capture from the engine's derived value, banding it to a level."""
        return cls(
            value=value,
            level=cls.level_for(value),
            method=method,
            total_signals=total_signals,
            skipped_signals=skipped_signals,
            triggered_fallback=triggered_fallback,
        )

    @staticmethod
    def level_for(value: float) -> ConfidenceLevel:
        if value >= HIGH_MIN:
            return ConfidenceLevel.HIGH
        if value < LOW_MAX:
            return ConfidenceLevel.LOW
        return ConfidenceLevel.MEDIUM
