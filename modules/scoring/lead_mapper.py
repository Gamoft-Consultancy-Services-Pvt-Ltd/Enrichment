"""Ingestion boundary for Epic 5 enriched-lead payloads.

This is the ONLY place that knows the shape of the enriched-lead JSON.
Today it assumes our own structure; when Epic 5 finalizes its output
format, only the inside of this function changes — the engine, schemas,
and tests downstream never see the external format.
"""

from __future__ import annotations

from typing import Any

from schemas.lead_features import LeadFeatures


def map_enriched_lead(raw: dict[str, Any]) -> LeadFeatures:
    return LeadFeatures(
        lead_id=raw["lead_id"],
        tenant_id=raw["tenant_id"],
        fields=raw.get("fields", {}),
        signal_values=raw.get("signal_values", {}),
        field_confidence=raw.get("field_confidence", {}),
    )