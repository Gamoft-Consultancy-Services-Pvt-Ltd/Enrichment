"""The one LLM call in scoring: answer every signal question for one lead.

One call per lead, not per signal. The model never sees weights or thresholds
and never produces a score — it only answers each signal's yes/no question and
cites the field it relied on. Arithmetic happens in scoring_engine.py.
"""

import json
import logging
from typing import Any

from clients.llm_client import call_with_tool
from modules.lead_ingestion.db.models import Lead
from modules.scoring.schemas import SignalJudgment, Verdict
from shared.events.schemas import EnrichmentResult
from shared.tenant_config.schemas import TenantConfigRead

logger = logging.getLogger(__name__)

_TOOL_NAME = "judge_signals"
_TOOL_DESCRIPTION = (
    "Answer each lead-scoring signal question using ONLY the supplied lead and "
    "research data. Return one judgment per signal_id. Use SATISFIED when the "
    "data shows the signal is true, NOT_SATISFIED when it shows it is false, and "
    "UNKNOWN when the data does not answer the question. Never guess: UNKNOWN is "
    "always better than an unsupported answer. Every judgment must cite the field "
    "it relied on in `evidence`."
)
_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "judgments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "signal_id": {"type": "string"},
                    "verdict": {
                        "type": "string",
                        "enum": ["SATISFIED", "NOT_SATISFIED", "UNKNOWN"],
                    },
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "evidence": {
                        "type": "string",
                        "description": "The data field supporting this verdict.",
                    },
                },
                "required": ["signal_id", "verdict", "confidence", "evidence"],
            },
        }
    },
    "required": ["judgments"],
}


async def judge_signals(
    config: TenantConfigRead, enrichment: EnrichmentResult, lead: Lead
) -> list[SignalJudgment]:
    """Ask the LLM to answer every signal question for this lead."""
    raw = await call_with_tool(
        prompt=_build_prompt(config, enrichment, lead),
        tool_name=_TOOL_NAME,
        tool_description=_TOOL_DESCRIPTION,
        input_schema=_INPUT_SCHEMA,
        max_tokens=4096,
    )
    return _parse(raw, config)


def _build_prompt(config: TenantConfigRead, enrichment: EnrichmentResult, lead: Lead) -> str:
    """Business context + signals + everything known about the lead."""
    signals = "\n".join(f"- {s.id} ({s.dimension.value}): {s.question}" for s in config.signals)
    lead_data = {
        "full_name": lead.full_name,
        "email": lead.email,
        "location": lead.location,
        "source_channel": lead.source_channel,
        "extra_fields": lead.extra_fields,
    }
    return (
        "You are scoring an inbound lead for a business.\n\n"
        f"## The business\n{json.dumps(config.business_profile, indent=2)}\n\n"
        f"## Their ideal customer profile\n{json.dumps(config.icp, indent=2)}\n\n"
        f"## The lead (first-party data, as it arrived)\n"
        f"{json.dumps(lead_data, indent=2, default=str)}\n\n"
        f"## Research findings about the lead\n"
        f"{json.dumps(enrichment.model_dump(mode='json'), indent=2)}\n\n"
        f"## Signals to judge\n{signals}\n\n"
        "Answer every signal listed above, exactly once each, using only the data "
        "shown. If the data does not answer a question, return UNKNOWN."
    )


def _parse(raw: dict[str, Any], config: TenantConfigRead) -> list[SignalJudgment]:
    """Keep judgements for known signals; drop anything the model invented."""
    known = {s.id for s in config.signals}
    judgments: list[SignalJudgment] = []
    seen: set[str] = set()

    for item in raw.get("judgments", []):
        signal_id = str(item.get("signal_id", ""))
        if signal_id not in known:
            logger.warning("scoring: dropping unrecognised signal_id %r", signal_id)
            continue
        if signal_id in seen:
            continue
        seen.add(signal_id)
        judgments.append(
            SignalJudgment(
                signal_id=signal_id,
                verdict=Verdict(item.get("verdict", Verdict.UNKNOWN)),
                confidence=float(item.get("confidence", 0.0)),
                evidence=str(item.get("evidence", "")),
            )
        )

    # Signals the model skipped are simply absent; the engine treats a missing
    # judgement as UNKNOWN, so no placeholder is needed here.
    if missing := known - seen:
        logger.info("scoring: %d signals unanswered by the model: %s", len(missing), missing)
    return judgments
