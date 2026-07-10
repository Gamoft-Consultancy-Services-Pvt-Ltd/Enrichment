"""Unit tests for the web_search MCP server — mocks serper."""

from unittest.mock import AsyncMock, patch


async def test_web_search_delegates_to_serper() -> None:
    with patch(
        "mcp_servers.web_search.server.serper_client.search",
        AsyncMock(return_value="a readable digest"),
    ) as mock_search:
        from mcp_servers.web_search.server import web_search

        out = await web_search("acme crm", 3)

    assert out == "a readable digest"
    mock_search.assert_awaited_once_with("acme crm", num=3)
