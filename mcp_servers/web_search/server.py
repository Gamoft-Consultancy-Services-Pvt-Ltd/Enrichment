"""Standalone MCP server exposing a single `web_search` tool backed by Serper.

Runs over streamable-http. The Serper API key lives only in this process's env;
the research agent connects by URL and never sees the key.
"""

import os

from mcp.server.fastmcp import FastMCP

from clients import serper_client

# Read host/port from FASTMCP_* env explicitly and pass them in: FastMCP.__init__
# defaults host to "127.0.0.1" and passes that default into its Settings, which
# overrides the FASTMCP_HOST env var (an explicit kwarg beats env in pydantic-settings).
# Binding 0.0.0.0 also avoids FastMCP's localhost-only DNS-rebinding guard, so other
# containers (app, worker) can reach the server over the compose network.
mcp = FastMCP(
    "web-search",
    host=os.getenv("FASTMCP_HOST", "127.0.0.1"),
    port=int(os.getenv("FASTMCP_PORT", "8000")),
)


@mcp.tool()
async def web_search(query: str, num: int = 5) -> str:
    """Search the web for `query` and return a readable digest of the top results.

    Use this to research a company, person, or topic. Compose a focused query;
    call again with a refined query if the first result is not enough.
    """
    return await serper_client.search(query, num=num)


def main() -> None:
    """Run the MCP server over streamable-http (host/port from FastMCP settings)."""
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
