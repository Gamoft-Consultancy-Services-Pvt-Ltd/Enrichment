"""
COMP-1201 — Scoring provenance model (Epic 6, Domain 12: Explainability & Provenance)

A typed record of *what produced a score*, so any score can later be explained
and audited (GDPR Art. 22 / accountability). Replaces the loose dict that the
runtime currently folds into ScoringResult.breakdown["provenance"] with a
validated Pydantic model, while keeping the exact same storage location (the
breakdown JSONB) — so no DB schema change is needed now.

Sub-task coverage:
- ST1: this model (ScoringProvenance) defines the provenance data structure.
- ST2: evidence_sources — where the lead's facts came from (enrichment).
- ST3: signal_sources — which signals/signal-set produced the score.
- ST4: prompt_version — set when the LLM fallback ran.
- ST5: model_version — set when the LLM fallback ran.

The model is deliberately permissive about the LLM fields: they are Optional and
only populated on the fallback path, so a purely deterministic score is still a
complete, valid provenance record (with scorer="deterministic_engine").
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class ScorerType(str, Enum):
    """Which scorer produced the final score."""

    DETERMINISTIC = "deterministic_engine"
    LLM_FALLBACK = "llm_fallback"


class EvidenceSource(BaseModel):
    """ST2 — one piece of evidence behind the lead's facts.

    Typically populated from the enrichment layer (Epic 5). Kept loose enough to
    accept whatever the upstream provides without over-constraining the contract.
    """

    field: str = Field(..., description="The lead field this evidence supports")
    source: str | None = Field(
        default=None, description="Origin, e.g. 'serper', 'shopify', 'tenant_input'"
    )
    url: str | None = Field(default=None, description="Source URL if applicable")
    retrieved_at: str | None = Field(
        default=None, description="ISO timestamp of when the fact was retrieved"
    )
    confidence: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Field confidence from enrichment"
    )


class ScoringProvenance(BaseModel):
    """ST1 — the full provenance record attached to a ScoringResult."""

    # --- what configuration scored the lead (ST3) ---------------------- #
    scorer: ScorerType = Field(..., description="Deterministic engine or LLM fallback")
    signal_set_version: int = Field(..., description="Active SignalSet version used")
    signal_set_source: str | None = Field(
        default=None, description="Pipeline that produced the SignalSet (e.g. onboarding)"
    )
    signal_names: list[str] = Field(
        default_factory=list, description="Signals available to the scorer (ST3)"
    )

    # --- result confidence (COMP-1203-ST4 also reads this) ------------- #
    confidence: float = Field(
        ..., ge=0.0, le=1.0, description="Derived result confidence (0-1)"
    )

    # --- LLM fallback versions (ST4 / ST5) — only when scorer=LLM ------- #
    prompt_version: str | None = Field(
        default=None, description="Fallback prompt version (ST4)"
    )
    model_version: str | None = Field(
        default=None, description="Fallback model identifier (ST5)"
    )

    # --- evidence behind the lead's facts (ST2) ------------------------ #
    evidence_sources: list[EvidenceSource] = Field(default_factory=list)

    # --- bookkeeping --------------------------------------------------- #
    recorded_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="When this provenance record was assembled",
    )

    @model_validator(mode="after")
    def _llm_versions_present_for_llm_scorer(self) -> "ScoringProvenance":
        """If the LLM fallback scored, its prompt and model versions must be set —
        otherwise the record can't actually explain an automated decision (the
        whole point of COMP-1201). Deterministic scores must NOT carry them."""
        if self.scorer is ScorerType.LLM_FALLBACK:
            if not self.prompt_version or not self.model_version:
                raise ValueError(
                    "LLM-fallback provenance requires prompt_version and model_version"
                )
        else:
            # keep deterministic records clean and unambiguous
            if self.prompt_version or self.model_version:
                raise ValueError(
                    "Deterministic provenance must not carry LLM prompt/model versions"
                )
        return self

    # convenience: round-trip helpers for the breakdown JSONB ----------- #
    def to_storage(self) -> dict[str, Any]:
        """Serialise for breakdown['provenance']."""
        return self.model_dump(mode="json")

    @classmethod
    def from_storage(cls, data: dict[str, Any]) -> "ScoringProvenance":
        """Rehydrate from breakdown['provenance'] for the retrieval API."""
        return cls.model_validate(data)
