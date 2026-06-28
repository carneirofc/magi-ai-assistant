"""Tests for the Seanime-over-MCP tool surface (agent.tools.seanime_mcp) and the
config-driven choice of anime specialist (agent.members.seanime).

No MCP server is contacted: `build_seanime_mcp_tools` returns an *unconnected*
agno toolkit, so these assert how it's wired (endpoint, transport, auth header)
and that `build_seanime_specialist` swaps between the direct-HTTP and MCP
variants on `config.seanime_use_mcp`. The whole module is skipped when the
optional `mcp` extra isn't installed.
"""

import pytest

pytest.importorskip("mcp", reason="needs the optional `mcp` extra (uv sync --extra mcp)")

from agent.members.seanime import build_seanime_specialist  # noqa: E402
from agent.tools.seanime_mcp import (  # noqa: E402
    SEANIME_MCP_TOOL_NAMES,
    build_seanime_mcp_tools,
    is_mcp_toolkit,
)
from core.config import config, configure  # noqa: E402


def _restore_seanime_config():
    before = {
        "seanime_use_mcp": config.seanime_use_mcp,
        "seanime_mcp_url": config.seanime_mcp_url,
        "seanime_token": config.seanime_token,
    }
    return before


def test_build_targets_configured_endpoint_over_streamable_http():
    before = _restore_seanime_config()
    try:
        configure(seanime_mcp_url="http://example.test:9/api/v1/mcp", seanime_token=None)
        tools = build_seanime_mcp_tools()
        assert tools.transport == "streamable-http"
        assert tools.server_params.url == "http://example.test:9/api/v1/mcp"
        # No token configured -> no auth header at all.
        assert tools.server_params.headers is None
        # All read-only tools are marked to surface their result, like the
        # direct tools' show_result=True.
        assert set(tools.show_result_tools) == set(SEANIME_MCP_TOOL_NAMES)
    finally:
        configure(**before)


def test_auth_header_rides_only_when_token_configured():
    before = _restore_seanime_config()
    try:
        configure(seanime_token="hash123")
        tools = build_seanime_mcp_tools()
        assert tools.server_params.headers == {"X-Seanime-Token": "hash123"}
    finally:
        configure(**before)


def test_is_mcp_toolkit_duck_types_the_class():
    assert is_mcp_toolkit(build_seanime_mcp_tools()) is True
    assert is_mcp_toolkit(object()) is False


def test_specialist_uses_direct_tools_by_default():
    before = _restore_seanime_config()
    try:
        configure(seanime_use_mcp=False)
        agent = build_seanime_specialist(None)
        assert not any(is_mcp_toolkit(t) for t in agent.tools)
        # The direct surface is the static @tool functions (seanime_* + media).
        assert len(agent.tools) > 1
    finally:
        configure(**before)


def test_specialist_uses_mcp_when_enabled():
    before = _restore_seanime_config()
    try:
        configure(seanime_use_mcp=True)
        agent = build_seanime_specialist(None)
        assert any(is_mcp_toolkit(t) for t in agent.tools)
    finally:
        configure(**before)
