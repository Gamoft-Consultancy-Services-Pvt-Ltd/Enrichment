"""Thin async wrapper around the Groq Python SDK.

The only file in the project that imports `groq`. All LLM calls go through
`call_with_tool`, which forces structured output via function calling.
"""

import json
from typing import Any

from groq import AsyncGroq

from core.config import get_settings
from core.exceptions import ExternalServiceError

_MODEL = "llama-3.3-70b-versatile"

_CLASSIFY_TOOL_NAME = "classify_message"
_CLASSIFY_TOOL_DESCRIPTION = (
    "Classify an inbound message as LEAD, NOISE, UNCLEAR, or EXISTING_CUSTOMER. "
    "Extract any identity fields (name, phone, email, location, intent) present. "
    "Return confidence between 0 and 1. If uncertain, classify as LEAD."
)
_CLASSIFY_INPUT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "classification": {
            "type": "string",
            "enum": ["LEAD", "NOISE", "UNCLEAR", "EXISTING_CUSTOMER"],
        },
        "extracted_fields": {
            "type": "object",
            "description": "Any identity or intent fields found in the message.",
        },
        "confidence": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
        },
    },
    "required": ["classification", "extracted_fields", "confidence"],
}


async def call_with_tool(
    *,
    prompt: str,
    tool_name: str,
    tool_description: str,
    input_schema: dict[str, Any],
    model: str = _MODEL,
    max_tokens: int = 2048,
) -> dict[str, Any]:
    """Call Groq with a single tool, forcing structured JSON output.

    Returns the tool call's parsed arguments dict. Raises ExternalServiceError
    on any API failure or if the model returns no tool call.
    """
    client = AsyncGroq(api_key=get_settings().groq_api_key)
    try:
        response = await client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            temperature=0.0,
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "description": tool_description,
                        "parameters": input_schema,
                    },
                }
            ],
            tool_choice={"type": "function", "function": {"name": tool_name}},
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        raise ExternalServiceError(f"Groq API call failed: {exc}") from exc

    tool_calls = response.choices[0].message.tool_calls
    if not tool_calls:
        raise ExternalServiceError("Groq returned no tool_call in response")

    result: dict[str, Any] = json.loads(tool_calls[0].function.arguments)
    return result


async def classify_message(text: str) -> dict[str, Any]:
    """Stage 2 noise filter: classify one message via Groq function calling.

    Returns a raw dict (classification, extracted_fields, confidence). Callers in
    modules/ must not import FilterResult from here — clients/ cannot import from
    modules/. The dict keys match FilterResult's fields so callers can construct it.

    Raises ExternalServiceError on API failure.
    """
    return await call_with_tool(
        prompt=text,
        tool_name=_CLASSIFY_TOOL_NAME,
        tool_description=_CLASSIFY_TOOL_DESCRIPTION,
        input_schema=_CLASSIFY_INPUT_SCHEMA,
    )
