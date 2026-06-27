"""
LEAD-52 — Rating fallback client (Epic 6: Lead Scoring Runtime)

A Sonnet-backed fallback that estimates whether SKIPPED signals would have
fired, for low/medium-confidence deterministic results. It never produces a
score and never overrides a fired signal or a hard block — the deterministic
engine remains the sole scorer (Epic 6 architectural decision). The client's
only job is to return per-signal would_fire estimates plus a reasoning string;
the agent (LEAD-53) feeds those back into the engine and recomputes.

Design notes:
- LEAD-52-S2: Sonnet runs in an isolated, single-purpose call — no tools, no
  chained state, a strict JSON-only contract — mirroring the onboarding agents.
- LEAD-52-S5: transient failures are retried with backoff.
- LEAD-52-S6: on hard failure the client raises RatingClientError so the caller
  (LEAD-53) can degrade gracefully to the deterministic result.
- LEAD-52-S7: logging never includes raw lead PII — only ids, counts, outcomes.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger("scoring.rating_client")

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 1024
DEFAULT_MAX_RETRIES = 2
DEFAULT_BACKOFF_SECONDS = 0.5
DEFAULT_TIMEOUT_SECONDS = 20


# ---------------------------------------------------------------------------
# LEAD-52-S3 — request schema
# ---------------------------------------------------------------------------

class SkippedSignalContext(BaseModel):
    """One skipped signal the LLM is asked to estimate."""

    model_config = ConfigDict(extra="forbid")

    signal_id: str
    dimension: str
    observation: str = Field(..., description="Human-readable rule, no raw PII")


class RatingRequest(BaseModel):
    """Everything the fallback needs — and nothing it doesn't.

    `lead_summary` is a redacted, human-readable digest assembled by the agent;
    raw enriched fields are never sent wholesale.
    """

    model_config = ConfigDict(extra="forbid")

    lead_id: str
    tenant_id: str
    lead_summary: str = Field(..., description="Redacted context digest")
    skipped_signals: list[SkippedSignalContext]


# ---------------------------------------------------------------------------
# LEAD-52-S4 — response schema
# ---------------------------------------------------------------------------

class SignalEstimate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signal_id: str
    would_fire: bool
    rationale: str = ""


class RatingResponse(BaseModel):
    """Structured fallback output. Validated before the agent trusts it."""

    model_config = ConfigDict(extra="forbid")

    estimates: list[SignalEstimate] = Field(default_factory=list)
    overall_reasoning: str = ""


class RatingClientError(RuntimeError):
    """Raised on unrecoverable failure so the caller can fall back (S6)."""


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You are a lead-scoring evidence estimator. You are given a redacted lead "
    "summary and a list of scoring signals that could NOT be evaluated "
    "deterministically because the underlying data was missing. For each "
    "skipped signal, estimate whether it WOULD fire based only on the summary. "
    "You do not assign scores. You do not re-evaluate signals that already "
    "fired. Respond with ONLY a JSON object, no prose, no markdown fences, of "
    'the exact form: {"estimates":[{"signal_id":str,"would_fire":bool,'
    '"rationale":str}],"overall_reasoning":str}. If the summary gives no basis '
    "to judge a signal, set would_fire to false."
)


def _build_user_prompt(request: RatingRequest) -> str:
    lines = [
        f"Lead summary:\n{request.lead_summary}\n",
        "Skipped signals to estimate:",
    ]
    for s in request.skipped_signals:
        lines.append(f"- {s.signal_id} [{s.dimension}]: {s.observation}")
    lines.append(
        "\nReturn a would_fire estimate for every signal_id listed above."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# The client (LEAD-52-S1)
# ---------------------------------------------------------------------------

class RatingClient:
    """Thin, isolated Sonnet caller. Pass any object exposing the Anthropic
    messages API as `client` (the real Anthropic SDK, or a fake in tests)."""

    def __init__(
        self,
        client: Any,
        *,
        model: str = MODEL,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
        sleep: Any = time.sleep,
    ):
        self._client = client
        self._model = model
        self._max_retries = max_retries
        self._backoff = backoff_seconds
        self._sleep = sleep  # injectable so tests don't actually wait

    def estimate(self, request: RatingRequest) -> RatingResponse:
        """Call Sonnet and return validated estimates.

        Raises RatingClientError on unrecoverable failure (S6).
        """
        if not request.skipped_signals:
            # nothing to estimate — return empty, no API call wasted
            return RatingResponse(estimates=[], overall_reasoning="no skipped signals")

        last_error: Optional[Exception] = None
        for attempt in range(self._max_retries + 1):
            try:
                raw = self._call_model(request)
                response = self._parse(raw, request)
                logger.info(
                    "rating_ok lead=%s tenant=%s skipped=%d estimated=%d attempt=%d",
                    request.lead_id, request.tenant_id,
                    len(request.skipped_signals), len(response.estimates), attempt,
                )
                return response
            except Exception as exc:  # noqa: BLE001 - we re-raise as RatingClientError
                last_error = exc
                # LEAD-52-S7: log type only, never raw lead data
                logger.warning(
                    "rating_attempt_failed lead=%s attempt=%d error=%s",
                    request.lead_id, attempt, type(exc).__name__,
                )
                if attempt < self._max_retries:
                    self._sleep(self._backoff * (2 ** attempt))  # exp. backoff

        # LEAD-52-S6: exhausted retries -> hard failure for graceful degradation
        raise RatingClientError(
            f"rating fallback failed after {self._max_retries + 1} attempts: "
            f"{type(last_error).__name__}"
        ) from last_error

    # ------------------------------------------------------------------

    def _call_model(self, request: RatingRequest) -> str:
        """One isolated Sonnet call (S2). Returns the raw text content."""
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=MAX_TOKENS,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _build_user_prompt(request)}],
        )
        # Concatenate text blocks; ignore any non-text content defensively.
        parts = []
        for block in getattr(resp, "content", []):
            text = getattr(block, "text", None)
            if text:
                parts.append(text)
        if not parts:
            raise ValueError("empty model response")
        return "".join(parts)

    def _parse(self, raw: str, request: RatingRequest) -> RatingResponse:
        """Strip fences, parse JSON, validate against RatingResponse."""
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            # tolerate ```json ... ``` even though we asked for none
            cleaned = cleaned.strip("`")
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:]
            cleaned = cleaned.strip()
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise ValueError(f"non-JSON model response: {exc}") from exc
        try:
            response = RatingResponse.model_validate(data)
        except ValidationError as exc:
            raise ValueError(f"response failed schema validation: {exc}") from exc

        # Guardrail: only keep estimates for signals we actually asked about.
        asked = {s.signal_id for s in request.skipped_signals}
        response.estimates = [e for e in response.estimates if e.signal_id in asked]
        return response
