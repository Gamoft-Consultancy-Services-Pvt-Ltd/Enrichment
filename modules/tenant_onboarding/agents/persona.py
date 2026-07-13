"""Persona agent — derives a structured business profile from website content."""

import json
from typing import Any

from clients.llm_client import call_with_tool
from shared.tenant.schemas import BusinessType

_TOOL_NAME = "output_business_profile"
_TOOL_DESCRIPTION = "Output the structured business profile extracted from the website."
_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "industry": {"type": "string"},
        "target_market": {"type": "string"},
        "products_services": {"type": "string"},
        "company_size": {"type": "string"},
        "geography": {"type": "string"},
        "value_proposition": {"type": "string"},
    },
    "required": [
        "industry",
        "target_market",
        "products_services",
        "company_size",
        "geography",
        "value_proposition",
    ],
}


async def run(
    company_name: str,
    business_type: BusinessType,
    company_info: dict[str, Any],
) -> dict[str, Any]:
    """Return a business_profile dict derived from researched company info."""
    prompt = (
        f"You are analyzing a business to build its profile.\n\n"
        f"Company name: {company_name}\n"
        f"Business type: {business_type.value}\n\n"
        f"Researched company information (JSON):\n{json.dumps(company_info, indent=2)}\n\n"
        f"Extract a structured business profile based only on the information above. "
        f"Be factual and concise."
    )
    result: dict[str, Any] = await call_with_tool(
        prompt=prompt,
        tool_name=_TOOL_NAME,
        tool_description=_TOOL_DESCRIPTION,
        input_schema=_INPUT_SCHEMA,
    )
    return result
