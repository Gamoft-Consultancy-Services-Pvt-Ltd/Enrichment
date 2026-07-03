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
