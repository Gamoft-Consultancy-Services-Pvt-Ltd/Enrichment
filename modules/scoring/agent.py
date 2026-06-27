"""
LEAD-53 — LeadScoringAgent (Epic 6: Lead Scoring Runtime)

Orchestrates deterministic scoring with an OPTIONAL Sonnet fallback. The
deterministic engine is always the scorer; the fallback only fills in
estimates for signals the engine had to skip, after which the engine
re-scores. The LLM never assigns a score and never touches fired signals
or hard blocks.

Control flow (acceptance criteria):
- LEAD-53-S2: run the engine first, always.
- LEAD-53-S3: if confidence is HIGH, return as-is (no LLM call).
- LEAD-53-S4: if BLOCKED, return as-is (no LLM call).
- LEAD-53-S5: if confidence is MEDIUM/LOW and fallback enabled, call the client.
- LEAD-53-S6/S7: apply would_fire estimates and recompute via the engine.
- LEAD-53-S8/S9: attach LLM reasoning, set llm_adjusted=True.
- LEAD-53-S10: if the client fails, return the deterministic result unchanged.
- LEAD-53-S11: in batch mode, fallback is forced OFF.
"""

from __future__ import annotations

import logging
from typing import Optional

from engine import ScoringEngine
from rating_client import (
    RatingClient,
    RatingClientError,
    RatingRequest,
    SkippedSignalContext,
)
from schemas.lead_features import LeadFeatures
from schemas.scoring_result import Classification, ConfidenceLevel, ScoringResult
from schemas.signal_set import SignalSet

logger = logging.getLogger("scoring.agent")


class LeadScoringAgent:
    """Deterministic-first scorer with optional LLM gap-filling."""

    def __init__(
        self,
        engine: Optional[ScoringEngine] = None,
        rating_client: Optional[RatingClient] = None,
    ):
        self.engine = engine or ScoringEngine()
        self.rating_client = rating_client  # None => fallback unavailable

    # ------------------------------------------------------------------

    def score(
        self,
        features: LeadFeatures,
        signal_set: SignalSet,
        *,
        enable_fallback: bool = True,
    ) -> ScoringResult:
        # LEAD-53-S2: deterministic engine runs first, always.
        result = self.engine.score(features, signal_set)

        # LEAD-53-S3 / S4: no LLM for high-confidence or blocked results.
        if not self._should_use_fallback(result, enable_fallback):
            return result

        # LEAD-53-S5: medium/low confidence + fallback enabled + client present.
        skipped = self._collect_skipped(result, signal_set)
        if not skipped:
            return result  # nothing the LLM could add

        try:
            estimates = self._call_fallback(features, result, skipped)
        except RatingClientError:
            # LEAD-53-S10: graceful degradation — deterministic result unchanged.
            logger.warning("fallback failed lead=%s; returning deterministic result",
                           features.lead_id)
            return result

        # LEAD-53-S6/S7: feed estimates back through the engine and recompute.
        adjusted = self._recompute_with_estimates(
            features, signal_set, estimates.fired_signal_ids
        )

        # LEAD-53-S8/S9: attach reasoning, mark as LLM-adjusted.
        adjusted.llm_adjusted = True
        adjusted.scoring_notes = list(adjusted.scoring_notes) + [
            f"LLM fallback estimated {len(estimates.fired_signal_ids)} of "
            f"{len(skipped)} skipped signals as firing",
            f"LLM reasoning: {estimates.overall_reasoning}",
        ]
        return adjusted

    def score_batch(
        self, batch: list[LeadFeatures], signal_set: SignalSet
    ) -> list[ScoringResult]:
        """LEAD-53-S11: batch scoring forces fallback OFF for determinism
        and cost control — every lead is scored by the engine only."""
        return [
            self.score(f, signal_set, enable_fallback=False) for f in batch
        ]

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _should_use_fallback(self, result: ScoringResult, enabled: bool) -> bool:
        if not enabled or self.rating_client is None:
            return False
        if result.classification == Classification.BLOCKED:  # S4
            return False
        if result.confidence == ConfidenceLevel.HIGH:  # S3
            return False
        return True

    @staticmethod
    def _collect_skipped(
        result: ScoringResult, signal_set: SignalSet
    ) -> list[SkippedSignalContext]:
        """Build redacted context for each skipped signal (no raw PII)."""
        skipped: list[SkippedSignalContext] = []
        for dim, ds in result.dimension_scores.items():
            for sid in ds.skipped_signal_ids:
                sig = signal_set.get_signal(sid)
                if sig is not None:
                    skipped.append(SkippedSignalContext(
                        signal_id=sid, dimension=dim, observation=sig.observation,
                    ))
        return skipped

    def _call_fallback(self, features, result, skipped):
        request = RatingRequest(
            lead_id=features.lead_id,
            tenant_id=features.tenant_id,
            lead_summary=self._build_summary(features, result),
            skipped_signals=skipped,
        )
        response = self.rating_client.estimate(request)
        fired = [e.signal_id for e in response.estimates if e.would_fire]
        return _FallbackOutcome(fired_signal_ids=fired,
                                overall_reasoning=response.overall_reasoning)

    def _recompute_with_estimates(
        self, features: LeadFeatures, signal_set: SignalSet,
        fired_signal_ids: list[str],
    ) -> ScoringResult:
        """Inject would_fire=True estimates into a COPY of the lead's
        signal_values and re-run the deterministic engine. This is what
        keeps the engine the sole scorer — the LLM output is just more
        pre-computed signal values."""
        # map signal_id -> dimension for correct signal_values nesting
        merged = {dim: dict(vals) for dim, vals in features.signal_values.items()}
        for sid in fired_signal_ids:
            sig = signal_set.get_signal(sid)
            if sig is None:
                continue
            merged.setdefault(sig.dimension.value, {})[sid] = True

        adjusted_features = features.model_copy(update={"signal_values": merged})
        return self.engine.score(adjusted_features, signal_set)

    @staticmethod
    def _build_summary(features: LeadFeatures, result: ScoringResult) -> str:
        """A compact, redacted digest. Deliberately conservative about what
        lead data is exposed to the LLM — ids and dimension scores only."""
        dims = ", ".join(
            f"{d}:{ds.capped_points:g}/{ds.budget:g}"
            for d, ds in result.dimension_scores.items()
        )
        return (
            f"Deterministic score {result.total_score:g}/100 "
            f"({result.classification.value}); dimension points [{dims}]. "
            f"Some signals could not be evaluated due to missing enriched data."
        )


class _FallbackOutcome:
    """Internal carrier for parsed fallback results."""

    __slots__ = ("fired_signal_ids", "overall_reasoning")

    def __init__(self, fired_signal_ids: list[str], overall_reasoning: str):
        self.fired_signal_ids = fired_signal_ids
        self.overall_reasoning = overall_reasoning
