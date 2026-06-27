"""
COMP-801-ST4 — AI activity logging (Epic 6, Domain 8: Audit Logging)

Scoring's slice of the audit-logging story. The logging *framework* (transport,
storage, retention, the canonical event envelope) is Epic 1. Scoring's job is to
EMIT an AI-activity event for each scoring run, in the framework's shape, so that
every automated decision is traceable during an audit (GDPR Art. 22 / SOC2).

Design:
- AuditSink is a tiny Protocol the framework satisfies. Until the Epic 1 sink is
  wired, LoggingAuditSink writes structured JSON to the standard logger, so the
  events are real and inspectable in dev without blocking on Epic 1.
- build_scoring_audit_event produces the canonical event dict from a scoring
  outcome. It records WHO/WHAT/WHEN at the granularity an AI audit needs: tenant,
  lead, score, classification, whether the LLM ran, and the model/prompt version
  — without copying the lead's personal data into the log (the breakdown stays in
  the scoring table; the audit log references it by lead_id + record_id).
- The event_type and field names mirror the COMP-801 standard ("ai.activity")
  so scoring's events sit alongside user/admin/api events in one stream.
  >>> CONFIRM the exact envelope keys against Epic 1's COMP-801-ST1 standard.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Protocol

logger = logging.getLogger("audit.ai.scoring")

EVENT_TYPE = "ai.activity"
ACTIVITY_SCORING = "lead_scored"


class AuditSink(Protocol):
    """What the Epic 1 audit framework provides. Scoring only needs emit()."""

    async def emit(self, event: dict[str, Any]) -> None: ...


class LoggingAuditSink:
    """Fallback sink: structured JSON to the logger until Epic 1's sink is wired."""

    async def emit(self, event: dict[str, Any]) -> None:
        logger.info("AUDIT %s", json.dumps(event, separators=(",", ":")))


class NullAuditSink:
    """No-op sink for tests / when auditing is intentionally disabled."""

    async def emit(self, event: dict[str, Any]) -> None:  # pragma: no cover
        return None


def build_scoring_audit_event(
    *,
    tenant_id: str,
    lead_id: str,
    record_id: int,
    score: float,
    classification: str,
    used_llm: bool,
    model_version: str | None,
    prompt_version: str | None,
    signal_set_version: int,
    actor: str = "system:scoring-worker",
) -> dict[str, Any]:
    """Build the canonical AI-activity event for one scoring run.

    Note: no lead personal data is placed in the event — only identifiers and the
    decision outcome. The full breakdown remains in the scoring table, referenced
    by record_id, so the audit log stays lean and avoids duplicating PII.
    """
    return {
        "event_type": EVENT_TYPE,
        "activity": ACTIVITY_SCORING,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "actor": actor,
        "tenant_id": tenant_id,
        "resource": {"type": "lead", "id": lead_id, "score_record_id": record_id},
        "decision": {
            "score": score,
            "classification": classification,
            "automated": True,
            "llm_assisted": used_llm,
        },
        "ai": {
            "model_version": model_version,
            "prompt_version": prompt_version,
            "signal_set_version": signal_set_version,
            "scorer": "llm_fallback" if used_llm else "deterministic_engine",
        },
    }
