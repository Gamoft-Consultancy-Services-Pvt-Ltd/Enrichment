"""Reusable web-research agent (LangGraph) sharing one MCP-hosted web_search tool.

`research(goal, output_schema)` is generic: callers pass a goal string and a
pydantic output schema. It knows nothing about enrichment or onboarding.
"""

from typing import TYPE_CHECKING, cast

import structlog
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.errors import GraphRecursionError
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel

from clients.llm_client import get_chat_model
from core.config import get_settings
from core.exceptions import ConfigurationError, ExternalServiceError

if TYPE_CHECKING:
    from langfuse.callback import CallbackHandler

log = structlog.get_logger(__name__)

_RECURSION_LIMIT = 25

# Groq's llama models intermittently emit a malformed tool call that Groq rejects with
# a 400 `tool_use_failed`; the identical request usually succeeds on a retry. Retry the
# agent invocation a few times on that specific error (temperature stays 0).
_MAX_TOOL_RETRIES = 2


def _is_tool_use_failure(exc: BaseException) -> bool:
    """True if `exc` (or a grouped sub-exception) is Groq's `tool_use_failed` 400."""
    texts = [str(exc)]
    if isinstance(exc, BaseExceptionGroup):
        texts.extend(str(sub) for sub in exc.exceptions)
    return any("tool_use_failed" in t or "Failed to call a function" in t for t in texts)

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


async def research[T: BaseModel](
    *, goal: str, output_schema: type[T], fallback: T | None = None
) -> T:
    """Run the web-research agent toward `goal` and return `output_schema` filled in.

    If `fallback` is given, a non-converging run (the ReAct loop exhausting the
    recursion limit — common for sparse-data targets) returns `fallback` instead
    of failing, so an automated caller like onboarding is not hard-failed by a
    hard-to-research company. Without a fallback, that case still raises.

    Raises ConfigurationError if MCP_WEB_SEARCH_URL is unset, and ExternalServiceError
    if the MCP server is unreachable or the agent fails.
    """
    mcp_url = get_settings().mcp_web_search_url
    # Checked outside the try: an unset URL is our misconfiguration, and wrapping it as
    # ExternalServiceError would blame the upstream and read as a transient outage.
    if not mcp_url:
        raise ConfigurationError(
            "MCP_WEB_SEARCH_URL is not set, so the research agent has no web_search "
            "tool to call. Set it to the MCP server's URL "
            "(http://mcp-web-search:8000/mcp inside docker-compose)."
        )

    try:
        client = MultiServerMCPClient(
            {
                "web_search": {
                    "url": mcp_url,
                    "transport": "streamable_http",
                }
            }
        )
        tools = await client.get_tools()
        agent = create_react_agent(get_chat_model(), tools, response_format=output_schema)
        handler = _langfuse_handler()
        callbacks = [handler] if handler is not None else []
        messages = {"messages": [SystemMessage(content=_SYSTEM), HumanMessage(content=goal)]}
        config: RunnableConfig = {"recursion_limit": _RECURSION_LIMIT, "callbacks": callbacks}

        for attempt in range(_MAX_TOOL_RETRIES + 1):
            try:
                state = await agent.ainvoke(messages, config=config)
                return cast(T, state["structured_response"])
            except Exception as exc:
                if attempt < _MAX_TOOL_RETRIES and _is_tool_use_failure(exc):
                    log.warning(
                        "research agent hit Groq tool_use_failed; retrying",
                        attempt=attempt + 1,
                        max_attempts=_MAX_TOOL_RETRIES + 1,
                    )
                    continue
                # A recursion-limit exhaustion means the loop could not converge;
                # retrying cannot help. Degrade to the caller's fallback if given.
                if isinstance(exc, GraphRecursionError) and fallback is not None:
                    log.warning(
                        "research agent hit the recursion limit; returning fallback",
                        recursion_limit=_RECURSION_LIMIT,
                    )
                    return fallback
                raise
        raise AssertionError("unreachable: loop returns or raises")  # pragma: no cover
    except Exception as exc:
        raise ExternalServiceError(f"research agent failed: {exc}") from exc
