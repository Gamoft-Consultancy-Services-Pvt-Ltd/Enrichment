"""Signals agent — generates scoring signals, weights, and thresholds."""

import json
from typing import Any

from clients.llm_client import call_with_tool
from shared.tenant_config.schemas import Signal, Thresholds, Weights

_TOOL_NAME = "output_scoring_config"
_TOOL_DESCRIPTION = "Output the signals, weights, and thresholds for lead scoring."
_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "signals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "dimension": {
                        "type": "string",
                        "enum": ["FIT", "INTENT", "ENGAGEMENT", "BEHAVIOUR", "CONTEXT"],
                    },
                    "question": {"type": "string"},
                },
                "required": ["id", "dimension", "question"],
            },
        },
        "weights": {
            "type": "object",
            "properties": {
                "fit": {"type": "number"},
                "intent": {"type": "number"},
                "engagement": {"type": "number"},
                "behaviour": {"type": "number"},
                "context": {"type": "number"},
            },
            "required": ["fit", "intent", "engagement", "behaviour", "context"],
        },
        "thresholds": {
            "type": "object",
            "properties": {
                "hot": {"type": "integer"},
                "warm": {"type": "integer"},
            },
            "required": ["hot", "warm"],
        },
    },
    "required": ["signals", "weights", "thresholds"],
}


async def run(
    business_profile: dict[str, Any],
    icp_data: dict[str, Any],
) -> tuple[list[Signal], Weights, Thresholds]:
    """Return (signals, weights, thresholds) ready for TenantConfigCreate."""
    prompt = (
        f"You are building a lead scoring configuration.\n\n"
        f"Business profile:\n{json.dumps(business_profile, indent=2)}\n\n"
        f"Ideal customer profile:\n{json.dumps(icp_data, indent=2)}\n\n"
        f"Generate:\n"
        f"1. Signals: yes/no questions identifying whether a lead matches this ICP. "
        f"Cover all five dimensions: FIT, INTENT, ENGAGEMENT, BEHAVIOUR, CONTEXT. "
        f"At least one signal per dimension. Use unique snake_case IDs.\n"
        f"2. Weights: how much each dimension matters (must sum to 1.0).\n"
        f"3. Thresholds: score cutoffs. Default: hot=80, warm=55."
    )
    result = await call_with_tool(
        prompt=prompt,
        tool_name=_TOOL_NAME,
        tool_description=_TOOL_DESCRIPTION,
        input_schema=_INPUT_SCHEMA,
    )
    sigs = [Signal.model_validate(s) for s in result["signals"]]
    weights = Weights.model_validate(result["weights"])
    thresholds = Thresholds.model_validate(result["thresholds"])
    return sigs, weights, thresholds
