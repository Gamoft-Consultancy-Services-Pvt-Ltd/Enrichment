"""Unit tests for two_stage_filter — no DB, no network.

Stage 1 (rule-based) tests confirm Groq is never called for known noise.
Stage 2 tests confirm all four FilterResult classifications are reachable.
"""

from unittest.mock import AsyncMock, patch

import pytest

from modules.lead_ingestion.schemas.filter_result import FilterClassification
from modules.lead_ingestion.two_stage_filter import run_filter

# ---------------------------------------------------------------------------
# Stage 1: rule-based discards — Groq must NOT be called
# ---------------------------------------------------------------------------

_NOISE_MESSAGES = [
    "👍",
    "😂😂😂",
    "hi",
    "Hi",
    "HI",
    "hello",
    "Hello",
    "hey",
    "ok",
    "Ok",
    "OK",
    "thanks",
    "Thanks",
    "k",
]


@pytest.mark.parametrize("text", _NOISE_MESSAGES)
async def test_stage1_discards_noise_without_llm(text: str) -> None:
    with patch("modules.lead_ingestion.two_stage_filter.classify_message") as mock_classify:
        result = await run_filter(text)

    assert result.classification == FilterClassification.NOISE
    mock_classify.assert_not_called()


async def test_stage1_passes_real_message_to_stage2() -> None:
    groq_response = {
        "classification": "LEAD",
        "extracted_fields": {"name": "Priya", "phone": "+919876543210"},
        "confidence": 0.95,
    }
    with patch(
        "modules.lead_ingestion.two_stage_filter.classify_message",
        new=AsyncMock(return_value=groq_response),
    ) as mock_classify:
        result = await run_filter("Hi, I'm interested in your premium plan")

    mock_classify.assert_called_once()
    assert result.classification == FilterClassification.LEAD


# ---------------------------------------------------------------------------
# Stage 2: all four classifications round-trip through Groq response
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "classification",
    [c.value for c in FilterClassification],
)
async def test_stage2_returns_all_classifications(classification: str) -> None:
    groq_response = {
        "classification": classification,
        "extracted_fields": {},
        "confidence": 0.8,
    }
    with patch(
        "modules.lead_ingestion.two_stage_filter.classify_message",
        new=AsyncMock(return_value=groq_response),
    ):
        result = await run_filter("I need help with pricing")

    assert result.classification == FilterClassification(classification)



async def test_stage2_extracted_fields_propagated() -> None:
    groq_response = {
        "classification": "LEAD",
        "extracted_fields": {"name": "Ravi", "email": "ravi@example.com"},
        "confidence": 0.9,
    }
    with patch(
        "modules.lead_ingestion.two_stage_filter.classify_message",
        new=AsyncMock(return_value=groq_response),
    ):
        result = await run_filter("My name is Ravi, email ravi@example.com")

    assert result.extracted_fields["name"] == "Ravi"
    assert result.extracted_fields["email"] == "ravi@example.com"


async def test_stage2_low_confidence_still_returns_result() -> None:
    groq_response = {"classification": "LEAD", "extracted_fields": {}, "confidence": 0.3}
    with patch(
        "modules.lead_ingestion.two_stage_filter.classify_message",
        new=AsyncMock(return_value=groq_response),
    ):
        result = await run_filter("interested")

    assert result.classification == FilterClassification.LEAD
    assert result.confidence == pytest.approx(0.3)


# ---------------------------------------------------------------------------
# Calibration: low-confidence non-LEAD results must be escalated to LEAD
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "original_class",
    ["NOISE", "EXISTING_CUSTOMER"],
)
async def test_stage2_low_confidence_escalated_to_lead(original_class: str) -> None:
    """Stage 2 result with confidence < 0.7 must be coerced to LEAD (CLAUDE.md rule)."""
    groq_response = {"classification": original_class, "extracted_fields": {}, "confidence": 0.6}
    with patch(
        "modules.lead_ingestion.two_stage_filter.classify_message",
        new=AsyncMock(return_value=groq_response),
    ):
        result = await run_filter("maybe interested in your product")

    assert result.classification == FilterClassification.LEAD
    assert result.confidence == pytest.approx(0.6)


async def test_stage2_unclear_not_escalated_in_filter() -> None:
    """UNCLEAR at any confidence stays UNCLEAR here; pipeline.py routes it as LEAD."""
    groq_response = {"classification": "UNCLEAR", "extracted_fields": {}, "confidence": 0.4}
    with patch(
        "modules.lead_ingestion.two_stage_filter.classify_message",
        new=AsyncMock(return_value=groq_response),
    ):
        result = await run_filter("maybe")

    assert result.classification == FilterClassification.UNCLEAR
