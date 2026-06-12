"""Two-stage noise filter for message-based inbound channels.

Stage 1 — rule-based (zero LLM calls): discards obvious non-leads
  (emoji-only, single greetings, one-word filler replies).

Stage 2 — Groq classify_message: full LLM classification for anything
  that passes Stage 1.

Calibration (CLAUDE.md): Stage 2 results with confidence < 0.7 are coerced to
LEAD here — a missed lead costs more than processing a non-lead.  UNCLEAR is
returned as-is; pipeline.py routes it to the LEAD path unconditionally.
"""

import re

from clients.groq_client import classify_message
from modules.lead_ingestion.exceptions import FilterClientError
from modules.lead_ingestion.schemas.filter_result import FilterClassification, FilterResult

# Single greetings / filler words that contain zero lead signal
_STAGE1_NOISE_WORDS: frozenset[str] = frozenset(
    {
        "hi", "hello", "hey", "ok", "okay", "thanks", "thank",
        "k", "yes", "no", "bye", "good", "fine", "sure",
    }
)

# Matches strings that are entirely Unicode emoji / whitespace / punctuation
_EMOJI_ONLY_RE = re.compile(
    r"^[\s\U0001F000-\U0001FFFF\U00002600-\U000027BF"
    r"\U0001FA00-\U0001FA9F\U00002702-\U000027B0"
    r"\U0001F900-\U0001F9FF\U00002194-\U00002199"
    r"\U00002300-\U000023FF\U00002B50\U00002B55"
    r"\U0000200D\U0000FE0F\U000020E3]+$"
)


def _is_stage1_noise(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    if _EMOJI_ONLY_RE.match(stripped):
        return True
    # Normalise to lower, strip punctuation, check against noise word list
    word = re.sub(r"[^\w]", "", stripped.lower())
    return word in _STAGE1_NOISE_WORDS


async def run_filter(text: str) -> FilterResult:
    """Run the two-stage filter on one inbound message text.

    Returns a FilterResult. Never raises on valid Groq responses; raises
    FilterClientError if Stage 2 returns an unrecognised classification.
    """
    if _is_stage1_noise(text):
        return FilterResult(classification=FilterClassification.NOISE)

    raw = await classify_message(text)

    try:
        classification = FilterClassification(raw["classification"])
    except (KeyError, ValueError) as exc:
        raise FilterClientError(
            f"Groq returned unrecognised classification: {raw.get('classification')!r}"
        ) from exc

    confidence: float | None = raw.get("confidence")
    # Calibration: low-confidence non-LEAD → escalate to LEAD.
    # UNCLEAR is intentionally excluded — pipeline.py already routes it as LEAD.
    if (
        confidence is not None
        and confidence < 0.7
        and classification not in (FilterClassification.LEAD, FilterClassification.UNCLEAR)
    ):
        classification = FilterClassification.LEAD

    return FilterResult(
        classification=classification,
        extracted_fields=raw.get("extracted_fields", {}),
        confidence=confidence,
    )
