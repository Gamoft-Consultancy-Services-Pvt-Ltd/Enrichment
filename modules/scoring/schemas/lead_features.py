"""
LEAD-45 — LeadFeatures schema (Epic 6: Lead Scoring Runtime)

Standardized input model for the scoring engine. Built from the Epic 5
enriched-lead payload so the engine never reads raw event JSON.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class LeadFeatures(BaseModel):
    """Everything the engine is allowed to know about one lead.

    - fields:           normalized enriched fields (nested dicts allowed)
    - signal_values:    pre-computed signal outcomes from the Epic 5
                        Signal Extractor, keyed [dimension][signal_id] -> bool
                        (Epic 2 Decision #1: the extractor pre-computes;
                        the engine prefers this path over raw conditions)
    - field_confidence: per-field confidence from enrichment, default 1.0
    """

    model_config = ConfigDict(extra="forbid")

    lead_id: str = Field(..., min_length=1)
    tenant_id: str = Field(..., min_length=1)
    fields: dict[str, Any] = Field(default_factory=dict)
    signal_values: dict[str, dict[str, bool]] = Field(default_factory=dict)
    field_confidence: dict[str, float] = Field(default_factory=dict)

    # ---- LEAD-45-S2: safe dot-path field accessor ------------------------

    def get_field(self, path: str, default: Any = None) -> Any:
        """Read `fields` by dot-path (e.g. "company.employee_count").

        Never raises: missing keys or non-dict intermediates return default.
        """
        node: Any = self.fields
        for part in path.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return default
        return node

    def has_field(self, path: str) -> bool:
        _MISSING = object()
        return self.get_field(path, _MISSING) is not _MISSING

    # ---- LEAD-45-S3: confidence accessor (default 1.0) -------------------

    def get_field_confidence(self, path: str) -> float:
        """Confidence for a field; 1.0 when enrichment didn't report one."""
        return float(self.field_confidence.get(path, 1.0))

    # ---- LEAD-45-S4 / S5: pre-computed signal value accessor --------------

    def get_signal_value(self, dimension: str, signal_id: str) -> Optional[bool]:
        """Pre-computed outcome for a signal, or None if absent.

        None tells the engine to fall back to condition evaluation
        (LEAD-51-S6) rather than treating the signal as not-fired.
        """
        return self.signal_values.get(dimension, {}).get(signal_id)
