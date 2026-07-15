"""Unit tests for shared/research/agent — mocks LangGraph + MCP."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.errors import GraphRecursionError
from pydantic import BaseModel

from core.exceptions import ConfigurationError, ExternalServiceError
from shared.research.agent import research
from tests.helpers import build_settings


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

    # non-tool errors are not retried — one attempt only
    assert fake_graph.ainvoke.await_count == 1


_TOOL_USE_FAILED = Exception(
    "Error code: 400 - {'error': {'message': 'Failed to call a function. ...', "
    "'code': 'tool_use_failed'}}"
)


async def test_research_retries_on_groq_tool_use_failed() -> None:
    """A tool_use_failed on the first invoke is retried; the retry's result is returned."""
    result = _Findings(summary="Acme makes CRM", confidence=0.9)
    make_agent, fake_graph = _patch_agent(None)
    fake_graph.ainvoke = AsyncMock(
        side_effect=[_TOOL_USE_FAILED, {"structured_response": result}]
    )

    with (
        patch("shared.research.agent.create_react_agent", make_agent),
        patch("shared.research.agent.MultiServerMCPClient") as mock_client_cls,
        patch("shared.research.agent.get_chat_model", MagicMock()),
        patch("shared.research.agent._langfuse_handler", MagicMock(return_value=MagicMock())),
    ):
        mock_client_cls.return_value.get_tools = AsyncMock(return_value=[MagicMock()])
        out = await research(goal="Research Acme Inc", output_schema=_Findings)

    assert out is result
    assert fake_graph.ainvoke.await_count == 2


async def test_research_gives_up_after_max_tool_retries() -> None:
    """Persistent tool_use_failed exhausts retries and raises ExternalServiceError."""
    make_agent, fake_graph = _patch_agent(None)
    fake_graph.ainvoke = AsyncMock(side_effect=_TOOL_USE_FAILED)

    with (
        patch("shared.research.agent.create_react_agent", make_agent),
        patch("shared.research.agent.MultiServerMCPClient") as mock_client_cls,
        patch("shared.research.agent.get_chat_model", MagicMock()),
        patch("shared.research.agent._langfuse_handler", MagicMock(return_value=MagicMock())),
    ):
        mock_client_cls.return_value.get_tools = AsyncMock(return_value=[MagicMock()])
        with pytest.raises(ExternalServiceError, match="tool_use_failed"):
            await research(goal="boom", output_schema=_Findings)

    # initial attempt + _MAX_TOOL_RETRIES (2) = 3 total
    assert fake_graph.ainvoke.await_count == 3


async def test_research_gives_agent_room_before_the_recursion_limit() -> None:
    """The ReAct loop gets enough steps (>= the LangGraph default) to converge."""
    result = _Findings(summary="Acme makes CRM", confidence=0.9)
    make_agent, fake_graph = _patch_agent(result)

    with (
        patch("shared.research.agent.create_react_agent", make_agent),
        patch("shared.research.agent.MultiServerMCPClient") as mock_client_cls,
        patch("shared.research.agent.get_chat_model", MagicMock()),
        patch("shared.research.agent._langfuse_handler", MagicMock(return_value=MagicMock())),
    ):
        mock_client_cls.return_value.get_tools = AsyncMock(return_value=[MagicMock()])
        await research(goal="Research Acme Inc", output_schema=_Findings)

    assert fake_graph.ainvoke.call_args.kwargs["config"]["recursion_limit"] == 25


async def test_research_returns_fallback_when_recursion_limit_hit() -> None:
    """A non-converging run (recursion limit) returns the caller's fallback, not an error."""
    fallback = _Findings(summary="", confidence=0.0)
    make_agent, fake_graph = _patch_agent(None)
    fake_graph.ainvoke = AsyncMock(
        side_effect=GraphRecursionError("Recursion limit of 25 reached")
    )

    with (
        patch("shared.research.agent.create_react_agent", make_agent),
        patch("shared.research.agent.MultiServerMCPClient") as mock_client_cls,
        patch("shared.research.agent.get_chat_model", MagicMock()),
        patch("shared.research.agent._langfuse_handler", MagicMock(return_value=MagicMock())),
    ):
        mock_client_cls.return_value.get_tools = AsyncMock(return_value=[MagicMock()])
        out = await research(goal="obscure co", output_schema=_Findings, fallback=fallback)

    assert out is fallback
    # non-convergence is not retried — a retry cannot converge either
    assert fake_graph.ainvoke.await_count == 1


async def test_research_raises_on_recursion_limit_without_fallback() -> None:
    """Without a fallback, a recursion-limit run still raises (unchanged for enrichment)."""
    make_agent, fake_graph = _patch_agent(None)
    fake_graph.ainvoke = AsyncMock(
        side_effect=GraphRecursionError("Recursion limit of 25 reached")
    )

    with (
        patch("shared.research.agent.create_react_agent", make_agent),
        patch("shared.research.agent.MultiServerMCPClient") as mock_client_cls,
        patch("shared.research.agent.get_chat_model", MagicMock()),
        patch("shared.research.agent._langfuse_handler", MagicMock(return_value=MagicMock())),
    ):
        mock_client_cls.return_value.get_tools = AsyncMock(return_value=[MagicMock()])
        with pytest.raises(ExternalServiceError, match="research agent failed"):
            await research(goal="obscure co", output_schema=_Findings)


async def test_research_raises_configuration_error_when_mcp_url_unset() -> None:
    """An unconfigured MCP URL is a config bug, not an upstream outage — say so."""
    with (
        patch(
            "shared.research.agent.get_settings",
            MagicMock(return_value=build_settings(mcp_web_search_url="")),
        ),
        patch("shared.research.agent.MultiServerMCPClient") as mock_client_cls,
    ):
        with pytest.raises(ConfigurationError, match="MCP_WEB_SEARCH_URL"):
            await research(goal="Research Acme Inc", output_schema=_Findings)

    # It must fail before dialling anything, and must not be masked as a 502.
    mock_client_cls.assert_not_called()
