"""Seanime tools over MCP — the alternative anime specialist's tool surface.

Where `agent/tools/seanime.py` hand-rolls HTTP calls against the Seanime REST
API, this module instead connects to Seanime's *built-in* Model Context Protocol
server (Streamable HTTP at `<seanime_base_url>/api/v1/mcp`, opt-in there via
`experimental.mcp`). Seanime publishes a small, read-only tool set there —
AniList search (anime/manga), base + extended media details, the signed-in
user's collection, and viewer stats — and agno's `MCPTools` toolkit discovers
them at connect time, so we don't redeclare any schemas here.

Only one anime specialist runs at a time: `config.seanime_use_mcp` selects this
MCP-backed variant over the direct-HTTP one. The Seanime *member* that wires
this surface is a persona specialist (e.g. alyssa.members.seanime), not an engine
one — the engine ships only the read-only tool mechanism here.
The trade-off is deliberate — the MCP surface is read-only and narrower (no
library files, missing-episode/schedule/continuity views, browse filters, or
progress mutations), but it rides Seanime's own contract instead of ours.

The `mcp` package is an optional dependency (`uv sync --extra mcp`); it's
imported lazily so the base install stays lean and only deployments that flip
`seanime_use_mcp` need it. The same `SEANIME_TOKEN` the REST tools use rides as
the `X-Seanime-Token` header (static, so the single connection is reused — no
per-run session churn).
"""

from typing import TYPE_CHECKING, Final

from magi.core.config import config

if TYPE_CHECKING:
    from agno.tools.mcp import MCPTools

# Read timeout for one MCP tool call, in seconds. AniList round-trips through
# Seanime, so allow a little headroom over a bare HTTP call.
_TIMEOUT_S: Final[int] = 30

# The read-only tools Seanime publishes over MCP (internal/mcp/tools.go). Listed
# so their results surface to the lead/client like the direct tools' show_result.
SEANIME_MCP_TOOL_NAMES: Final[tuple[str, ...]] = (
    "search_anime",
    "search_manga",
    "get_anime",
    "get_anime_details",
    "get_anime_collection",
    "get_viewer_stats",
)


def _headers() -> dict[str, str]:
    """Auth header for the MCP endpoint — only when the server has a password."""
    if config.seanime_token:
        return {"X-Seanime-Token": config.seanime_token}
    return {}


def build_seanime_mcp_tools() -> "MCPTools":
    """The Seanime MCP toolkit, pointed at `config.seanime_mcp_url`.

    Returns an unconnected `MCPTools`; agno connects it on the owning agent's
    first run (and the API pre-connects it at startup — see channels/api.py), at
    which point the server's tools are discovered and registered.
    """
    try:
        from agno.tools.mcp import MCPTools
        from agno.tools.mcp.params import StreamableHTTPClientParams
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise RuntimeError(
            "Seanime-over-MCP needs the optional 'mcp' dependency. "
            "Install it with `uv sync --extra mcp`, or set seanime_use_mcp=False "
            "to use the direct-HTTP Seanime tools instead."
        ) from exc

    params = StreamableHTTPClientParams(url=config.seanime_mcp_url, headers=_headers() or None)
    return MCPTools(
        server_params=params,
        transport="streamable-http",
        timeout_seconds=_TIMEOUT_S,
        show_result_tools=list(SEANIME_MCP_TOOL_NAMES),
    )


def is_mcp_toolkit(obj: object) -> bool:
    """True for an agno MCP toolkit instance, by duck-typing its class name so
    callers (the API's connect-at-startup hook) needn't import `mcp` themselves."""
    return any(c.__name__ in ("MCPTools", "MultiMCPTools") for c in type(obj).__mro__)
