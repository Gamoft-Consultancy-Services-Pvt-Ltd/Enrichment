"""Unit tests for clients/groq_client — mocks the Groq SDK."""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clients.groq_client import call_with_tool
from core.exceptions import ExternalServiceError


def _make_tool_call_response(tool_name: str, data: dict[str, Any]) -> MagicMock:
    tool_call = MagicMock()
    tool_call.function.name = tool_name
    tool_call.function.arguments = json.dumps(data)

    message = MagicMock()
    message.tool_calls = [tool_call]

    choice = MagicMock()
    choice.message = message

    response = MagicMock()
    response.choices = [choice]
    response.usage.prompt_tokens = 100
    response.usage.completion_tokens = 50
    return response


async def test_call_with_tool_returns_tool_input() -> None:
    expected = {"industry": "SaaS", "target_market": "SMB"}
    mock_create = AsyncMock(return_value=_make_tool_call_response("my_tool", expected))

    with patch("clients.groq_client.AsyncGroq") as mock_client_cls:
        mock_client_cls.return_value.chat.completions.create = mock_create
        result = await call_with_tool(
            prompt="test prompt",
            tool_name="my_tool",
            tool_description="desc",
            input_schema={"type": "object", "properties": {}, "required": []},
        )

    assert result == expected


async def test_call_with_tool_raises_on_api_error() -> None:
    with patch("clients.groq_client.AsyncGroq") as mock_client_cls:
        mock_client_cls.return_value.chat.completions.create = AsyncMock(
            side_effect=Exception("network error")
        )
        with pytest.raises(ExternalServiceError, match="Groq API call failed"):
            await call_with_tool(
                prompt="test",
                tool_name="tool",
                tool_description="desc",
                input_schema={"type": "object", "properties": {}, "required": []},
            )


async def test_call_with_tool_raises_when_no_tool_calls() -> None:
    message = MagicMock()
    message.tool_calls = None

    choice = MagicMock()
    choice.message = message

    response = MagicMock()
    response.choices = [choice]

    with patch("clients.groq_client.AsyncGroq") as mock_client_cls:
        mock_client_cls.return_value.chat.completions.create = AsyncMock(return_value=response)
        with pytest.raises(ExternalServiceError, match="no tool_call"):
            await call_with_tool(
                prompt="test",
                tool_name="tool",
                tool_description="desc",
                input_schema={"type": "object", "properties": {}, "required": []},
            )


async def test_call_with_tool_records_langfuse_generation() -> None:
    """The Langfuse observation gets model, prompt, structured output, and token usage."""
    expected = {"industry": "SaaS"}
    mock_create = AsyncMock(return_value=_make_tool_call_response("my_tool", expected))

    with (
        patch("clients.groq_client.AsyncGroq") as mock_client_cls,
        patch("clients.groq_client.langfuse_context") as mock_ctx,
    ):
        mock_client_cls.return_value.chat.completions.create = mock_create
        await call_with_tool(
            prompt="test prompt",
            tool_name="my_tool",
            tool_description="desc",
            input_schema={"type": "object", "properties": {}, "required": []},
        )

    mock_ctx.update_current_observation.assert_called_once()
    kwargs = mock_ctx.update_current_observation.call_args.kwargs
    assert kwargs["name"] == "my_tool"
    assert kwargs["model"] == "llama-3.3-70b-versatile"
    assert kwargs["input"] == "test prompt"
    assert kwargs["output"] == expected
    assert kwargs["usage"] == {"input": 100, "output": 50}


def test_get_chat_model_configures_groq(monkeypatch: pytest.MonkeyPatch) -> None:
    from clients import groq_client
    from tests.helpers import build_settings

    monkeypatch.setattr(
        groq_client, "get_settings", lambda: build_settings(groq_api_key="test-key")
    )
    model = groq_client.get_chat_model()
    assert model.model_name == "llama-3.3-70b-versatile"
    # langchain-groq normalizes temperature 0.0 -> 1e-8 (deterministic); see ChatGroq
    assert model.temperature == pytest.approx(0.0, abs=1e-6)
