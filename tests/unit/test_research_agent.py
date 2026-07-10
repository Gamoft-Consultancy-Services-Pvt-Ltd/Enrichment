"""Unit tests for shared/research/agent — mocks LangGraph + MCP."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

from core.exceptions import ExternalServiceError
from shared.research.agent import research


class _Findings(BaseModel):
    summary: str = ""
    confidence: float = 0.0


def _patch_agent(structured: Any) -> Any:
    """Return a patched create_react_agent whose graph returns `structured`."""
    fake_graph = MagicMock()
    fake_graph.ainvoke = AsyncMock(return_value={"structured_response": structured})
    return MagicMock(return_value=fake_graph), fake_graph


async def test_research_returns_structured_response() -> None:
    result = _Findings(summary="Acme makes CRM", confidence=0.9)
    make_agent, fake_graph = _patch_agent(result)

    with (
        patch("shared.research.agent.create_react_agent", make_agent),
        patch("shared.research.agent.MultiServerMCPClient") as mock_client_cls,
        patch("shared.research.agent.get_chat_model", MagicMock()),
        patch("shared.research.agent._langfuse_handler", MagicMock(return_value=MagicMock())),
    ):
        mock_client_cls.return_value.get_tools = AsyncMock(return_value=[MagicMock()])
        out = await research(goal="Research Acme Inc", output_schema=_Findings)

    assert out is result
    # goal reached the graph
    sent = fake_graph.ainvoke.call_args.args[0]
    assert any("Acme" in getattr(m, "content", "") for m in sent["messages"])
    # response_format wired to our schema
    assert make_agent.call_args.kwargs["response_format"] is _Findings


async def test_research_untraced_when_langfuse_handler_unavailable() -> None:
    """When _langfuse_handler() returns None, the graph runs with no callbacks."""
    result = _Findings(summary="Acme makes CRM", confidence=0.9)
    make_agent, fake_graph = _patch_agent(result)

    with (
        patch("shared.research.agent.create_react_agent", make_agent),
        patch("shared.research.agent.MultiServerMCPClient") as mock_client_cls,
        patch("shared.research.agent.get_chat_model", MagicMock()),
        patch("shared.research.agent._langfuse_handler", MagicMock(return_value=None)),
    ):
        mock_client_cls.return_value.get_tools = AsyncMock(return_value=[MagicMock()])
        await research(goal="Research Acme Inc", output_schema=_Findings)

    assert fake_graph.ainvoke.call_args.kwargs["config"]["callbacks"] == []


async def test_research_wraps_failures() -> None:
    make_agent, fake_graph = _patch_agent(None)
    fake_graph.ainvoke = AsyncMock(side_effect=Exception("mcp down"))

    with (
        patch("shared.research.agent.create_react_agent", make_agent),
        patch("shared.research.agent.MultiServerMCPClient") as mock_client_cls,
        patch("shared.research.agent.get_chat_model", MagicMock()),
        patch("shared.research.agent._langfuse_handler", MagicMock(return_value=MagicMock())),
    ):
        mock_client_cls.return_value.get_tools = AsyncMock(return_value=[MagicMock()])
        with pytest.raises(ExternalServiceError, match="research agent failed"):
            await research(goal="boom", output_schema=_Findings)
