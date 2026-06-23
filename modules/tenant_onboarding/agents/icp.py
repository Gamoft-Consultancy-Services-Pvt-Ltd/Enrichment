"""ICP agent — derives the ideal customer profile from a business profile."""

import json
from typing import Any

from clients.groq_client import call_with_tool

_TOOL_NAME = "output_icp"
_TOOL_DESCRIPTION = "Output the structured ideal customer profile."
_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "buyer_role": {"type": "string"},
        "company_size": {"type": "string"},
        "industry_vertical": {"type": "string"},
        "pain_points": {"type": "string"},
        "budget_range": {"type": "string"},
        "decision_timeline": {"type": "string"},
    },
    "required": [
        "buyer_role",
        "company_size",
        "industry_vertical",
        "pain_points",
        "budget_range",
        "decision_timeline",
    ],
}


async def run(business_profile: dict[str, Any]) -> dict[str, Any]:
    """Return an icp dict derived from the business profile."""
    prompt = (
        f"You are building an ideal customer profile (ICP) for a business.\n\n"
        f"Business profile:\n{json.dumps(business_profile, indent=2)}\n\n"
        f"Based on this business profile, define who their ideal customer is. "
        f"Be specific and actionable."
    )
    result: dict[str, Any] = await call_with_tool(
        prompt=prompt,
        tool_name=_TOOL_NAME,
        tool_description=_TOOL_DESCRIPTION,
        input_schema=_INPUT_SCHEMA,
    )
    return result
