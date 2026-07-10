"""Reusable web-research agent (LangGraph) sharing one MCP-hosted web_search tool.

`research(goal, output_schema)` is generic: callers pass a goal string and a
pydantic output schema. It knows nothing about enrichment or onboarding.
"""

from typing import TYPE_CHECKING, cast

import structlog
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel

from clients.groq_client import get_chat_model
from core.config import get_settings
from core.exceptions import ExternalServiceError

if TYPE_CHECKING:
    from langfuse.callback import CallbackHandler

log = structlog.get_logger(__name__)

_RECURSION_LIMIT = 12

_SYSTEM = (
    "You are a research agent. Use the web_search tool to gather the facts needed "
    "to fill in the requested structured output. Compose focused queries, read the "
    "results, and search again if you are not yet confident. Stop as soon as you "
    "have enough. Base every field only on what you found; do not invent facts."
)


def _langfuse_handler() -> "CallbackHandler | None":
    """Return a Langfuse callback handler, or None if unavailable (best-effort tracing).

    The import is deferred and guarded on purpose: langfuse 2.x's
    `langfuse.callback.CallbackHandler` requires the full legacy `langchain`
    package, which is unsatisfiable alongside this project's langchain-core 1.x
    pins (via langgraph / langchain-groq). Until `langchain` is added, tracing is
    best-effort — the agent still runs, just untraced. Isolated for test patching.
    """
    try:
        from langfuse.callback import CallbackHandler

        return CallbackHandler()
    except Exception as exc:
        # ImportError/ModuleNotFoundError (langchain absent) or any construction failure.
        log.warning("langfuse handler unavailable", error=str(exc))
        return None


async def research[T: BaseModel](*, goal: str, output_schema: type[T]) -> T:
    """Run the web-research agent toward `goal` and return `output_schema` filled in.

    Raises ExternalServiceError if the MCP server is unreachable or the agent fails.
    """
    try:
        client = MultiServerMCPClient(
            {
                "web_search": {
                    "url": get_settings().mcp_web_search_url,
                    "transport": "streamable_http",
                }
            }
        )
        tools = await client.get_tools()
        agent = create_react_agent(get_chat_model(), tools, response_format=output_schema)
        handler = _langfuse_handler()
        callbacks = [handler] if handler is not None else []
        state = await agent.ainvoke(
            {"messages": [SystemMessage(content=_SYSTEM), HumanMessage(content=goal)]},
            config={"recursion_limit": _RECURSION_LIMIT, "callbacks": callbacks},
        )
    except Exception as exc:
        raise ExternalServiceError(f"research agent failed: {exc}") from exc

    return cast(T, state["structured_response"])
