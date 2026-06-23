"""Thin async wrapper around the Groq Python SDK.

The only file in the project that imports `groq`. All LLM calls go through
`call_with_tool`, which forces structured output via function calling.
"""

import json
from typing import Any

from groq import AsyncGroq
from langfuse.decorators import langfuse_context, observe

from core.config import get_settings
from core.exceptions import ExternalServiceError

_MODEL = "llama-3.3-70b-versatile"


@observe(as_type="generation")
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
    usage = (
        {"input": response.usage.prompt_tokens, "output": response.usage.completion_tokens}
        if response.usage is not None
        else None
    )
    langfuse_context.update_current_observation(
        name=tool_name,
        model=model,
        input=prompt,
        output=result,
        usage=usage,
    )
    return result
