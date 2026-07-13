"""Thin async wrapper around an OpenAI-compatible LLM served by OpenRouter.

The project's LLM is **Qwen3-8B, served by OpenRouter**. OpenRouter speaks the
OpenAI API protocol, so the `openai` SDK and langchain's `ChatOpenAI` are used
purely as the OpenAI-*compatible* client — pointed at OpenRouter's base URL with
the OpenRouter API key. No OpenAI account or api.openai.com endpoint is involved.

All LLM calls go through `call_with_tool` (forces structured output via function
calling) or `get_chat_model` (LangGraph agents).
"""

import json
from typing import Any

from langchain_openai import ChatOpenAI
from langfuse.decorators import langfuse_context, observe
from openai import AsyncOpenAI
from openai.types.chat import (
    ChatCompletionMessageParam,
    ChatCompletionToolChoiceOptionParam,
    ChatCompletionToolParam,
)
from pydantic import SecretStr

from core.config import get_settings
from core.exceptions import ExternalServiceError

_MODEL = "qwen/qwen3-8b"

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


def _client() -> AsyncOpenAI:
    """OpenAI-compatible client pointed at OpenRouter."""
    settings = get_settings()
    return AsyncOpenAI(
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
    )


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
    """Call the LLM (via OpenRouter) with a single tool, forcing structured JSON output.

    Returns the tool call's parsed arguments dict. Raises ExternalServiceError
    on any API failure or if the model returns no tool call.
    """
    tools: list[ChatCompletionToolParam] = [
        {
            "type": "function",
            "function": {
                "name": tool_name,
                "description": tool_description,
                "parameters": input_schema,
            },
        }
    ]
    tool_choice: ChatCompletionToolChoiceOptionParam = {
        "type": "function",
        "function": {"name": tool_name},
    }
    messages: list[ChatCompletionMessageParam] = [{"role": "user", "content": prompt}]
    try:
        response = await _client().chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            temperature=0.0,
            tools=tools,
            tool_choice=tool_choice,
            messages=messages,
        )
    except Exception as exc:
        raise ExternalServiceError(f"LLM API call failed: {exc}") from exc

    tool_calls = response.choices[0].message.tool_calls
    if not tool_calls:
        raise ExternalServiceError("LLM returned no tool_call in response")

    tool_call = tool_calls[0]
    if tool_call.type != "function":
        raise ExternalServiceError(f"LLM returned a non-function tool call: {tool_call.type}")
    result: dict[str, Any] = json.loads(tool_call.function.arguments)
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


def get_chat_model() -> ChatOpenAI:
    """Return a ChatOpenAI pointed at OpenRouter for LangGraph agents (Qwen3-8B, temp 0)."""
    settings = get_settings()
    return ChatOpenAI(
        model=_MODEL,
        temperature=0.0,
        api_key=SecretStr(settings.openrouter_api_key),
        base_url=settings.openrouter_base_url,
    )


async def classify_message(text: str) -> dict[str, Any]:
    """Stage 2 noise filter: classify one message via LLM function calling.

    Returns a raw dict (classification, extracted_fields, confidence). Callers in
    modules/ must not import FilterResult from here — clients/ cannot import from
    modules/. The dict keys match FilterResult's fields so callers can construct it.

    Raises ExternalServiceError on API failure.
    """
    result: dict[str, Any] = await call_with_tool(
        prompt=text,
        tool_name=_CLASSIFY_TOOL_NAME,
        tool_description=_CLASSIFY_TOOL_DESCRIPTION,
        input_schema=_CLASSIFY_INPUT_SCHEMA,
    )
    return result
