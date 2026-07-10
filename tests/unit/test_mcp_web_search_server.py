"""Unit tests for the web_search MCP server — mocks serper."""

import importlib
from unittest.mock import AsyncMock, patch

import pytest


def test_mcp_binds_host_and_port_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression: FastMCP.__init__ defaults host to 127.0.0.1 and passes it into its
    # Settings, overriding the FASTMCP_HOST env var — so the server bound loopback and was
    # unreachable from other containers. server.py must read FASTMCP_HOST/PORT explicitly.
    import mcp_servers.web_search.server as server

    monkeypatch.setenv("FASTMCP_HOST", "0.0.0.0")
    monkeypatch.setenv("FASTMCP_PORT", "9123")
    importlib.reload(server)
    try:
        assert server.mcp.settings.host == "0.0.0.0"
        assert server.mcp.settings.port == 9123
    finally:
        monkeypatch.delenv("FASTMCP_HOST", raising=False)
        monkeypatch.delenv("FASTMCP_PORT", raising=False)
        importlib.reload(server)


async def test_web_search_delegates_to_serper() -> None:
    with patch(
        "mcp_servers.web_search.server.serper_client.search",
        AsyncMock(return_value="a readable digest"),
    ) as mock_search:
        from mcp_servers.web_search.server import web_search

        out = await web_search("acme crm", 3)

    assert out == "a readable digest"
    mock_search.assert_awaited_once_with("acme crm", num=3)
