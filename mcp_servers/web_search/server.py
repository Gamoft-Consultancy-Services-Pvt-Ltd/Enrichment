"""Standalone MCP server exposing a single `web_search` tool backed by Serper.

Runs over streamable-http. The Serper API key lives only in this process's env;
the research agent connects by URL and never sees the key.
"""

from mcp.server.fastmcp import FastMCP

from clients import serper_client

mcp = FastMCP("web-search")


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
