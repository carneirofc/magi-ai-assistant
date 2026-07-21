"""Read-only team introspection — a serializable snapshot of the running roster.

The chat API exposes this at `GET /v1/introspection` so an operator UI can see the
LIVE composition of the brain: the lead model, each specialist member with its
role/model/tools, the team-level tools, and any MCP servers a member connects to
(with their real connection status). It never runs the model — it only reads the
already-assembled `Team`/`Agent` object, so it's cheap and side-effect free.

Everything is duck-typed and defensive: a plain single-`Agent` runner (no members),
a member whose tools are raw callables, a toolkit with no discovered functions —
each degrades to a sensible partial snapshot instead of raising. The wire shape is
a public contract (the web BFF reads it), so version it, don't break it.
"""

from typing import Optional

from pydantic import BaseModel, Field

from magi.agent.tools.seanime_mcp import is_mcp_toolkit


# --- wire format --------------------------------------------------------------
class ToolInfo(BaseModel):
    """One callable the model can invoke, as the roster sees it."""

    name: str = Field(description="The tool name the model calls.")
    description: str = Field(default="", description="What the tool does / when to call it.")
    instructions: str = Field(default="", description="Operational guidance, if any.")
    source: str = Field(
        default="function",
        description="Where it comes from: 'function', 'toolkit:<name>', or 'mcp:<server>'.",
    )
    origin: str = Field(
        default="builtin",
        description=(
            "How the capability got here: 'builtin' (shipped with the engine), "
            "'recipe' (operator-approved HTTP recipe), 'registered' (persona "
            "toolkit), 'skill' (skill manifest), or 'mcp' (MCP server)."
        ),
    )


class McpServerInfo(BaseModel):
    """An MCP server a member connects to, plus its live connection status."""

    name: str = Field(description="Toolkit/server name.")
    transport: str = Field(default="", description="MCP transport (e.g. 'streamable-http').")
    url: str = Field(default="", description="Server URL, when the transport has one.")
    connected: bool = Field(default=False, description="Whether the session is currently open.")
    tools: list[str] = Field(default_factory=list, description="Tool names the server exposes.")
    member: str = Field(default="", description="The member that owns this toolkit.")


class MemberInfo(BaseModel):
    """One specialist on the team."""

    name: str = Field(description="Member id the lead routes to.")
    role: str = Field(default="", description="What the member specializes in.")
    model: str = Field(default="", description="Model id backing the member.")
    tools: list[ToolInfo] = Field(default_factory=list, description="The member's tools.")


class TeamSnapshot(BaseModel):
    """The whole running composition, read-only."""

    name: str = Field(description="Team (or agent) name.")
    lead_model: str = Field(default="", description="Model id backing the lead/router.")
    is_team: bool = Field(default=True, description="False for a bare single-agent runner.")
    members: list[MemberInfo] = Field(default_factory=list)
    team_tools: list[ToolInfo] = Field(default_factory=list, description="Team/lead-level tools.")
    mcp_servers: list[McpServerInfo] = Field(default_factory=list)


# --- serialization ------------------------------------------------------------
def _text(value: object) -> str:
    """A trimmed string for any attribute value (None/list tolerated)."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "\n".join(str(v) for v in value).strip()
    return str(value).strip()


def mark_origin(tools: list, origin: str) -> list:
    """Stamp each tool with its capability origin, best-effort; returns `tools`.

    Called at team assembly, where the origin is still known (which list a tool
    came from); the snapshot reads the stamp back so the roster can group by it.
    A tool that refuses the attribute just stays 'builtin'."""
    for tool in tools:
        try:
            tool._magi_origin = origin
        except Exception:  # noqa: BLE001 — an unstampable tool degrades to 'builtin'.
            pass
    return tools


def _function_info(fn: object, source: str = "function") -> ToolInfo:
    """A `ToolInfo` from an agno `Function`, a `@tool` callable, or a plain function."""
    name = _text(getattr(fn, "name", None) or getattr(fn, "__name__", None)) or type(fn).__name__
    description = _text(getattr(fn, "description", None) or getattr(fn, "__doc__", None))
    return ToolInfo(
        name=name,
        description=description,
        instructions=_text(getattr(fn, "instructions", None)),
        source=source,
        origin=_text(getattr(fn, "_magi_origin", None)) or "builtin",
    )


def _mcp_info(toolkit: object, member: str) -> McpServerInfo:
    """An `McpServerInfo` for an agno MCP toolkit, best-effort (unconnected is fine)."""
    server_params = getattr(toolkit, "server_params", None)
    functions = getattr(toolkit, "functions", None)
    # Prefer the server's discovered tools (populated on connect); fall back to the
    # tool set we asked to surface, so an unconnected toolkit still lists something.
    if isinstance(functions, dict) and functions:
        names = list(functions.keys())
    else:
        names = list(getattr(toolkit, "show_result_tools", None) or [])
    return McpServerInfo(
        name=_text(getattr(toolkit, "name", None)) or type(toolkit).__name__,
        transport=_text(getattr(toolkit, "transport", None)),
        url=_text(getattr(server_params, "url", None)),
        # agno's MCPTools holds a live `session` only while connected.
        connected=getattr(toolkit, "session", None) is not None,
        tools=[_text(n) for n in names],
        member=member,
    )


def _expand_tools(tools: object, member: str = "") -> tuple[list[ToolInfo], list[McpServerInfo]]:
    """Flatten a runner's/member's `tools` list into tool infos + any MCP servers.

    An MCP toolkit yields both an `McpServerInfo` (for the dedicated section) and
    per-tool `ToolInfo`s tagged `mcp:<server>` (so it also shows in the owner's tool
    list). A generic toolkit (e.g. Docker) is expanded into its discovered functions.
    """
    infos: list[ToolInfo] = []
    mcps: list[McpServerInfo] = []
    for tool in tools or []:
        if is_mcp_toolkit(tool):
            server = _mcp_info(tool, member)
            mcps.append(server)
            infos.extend(
                ToolInfo(name=n, source=f"mcp:{server.name}", origin="mcp") for n in server.tools
            )
            continue
        functions = getattr(tool, "functions", None)
        if isinstance(functions, dict) and functions:
            toolkit_name = _text(getattr(tool, "name", None)) or type(tool).__name__
            infos.extend(_function_info(fn, source=f"toolkit:{toolkit_name}") for fn in functions.values())
            continue
        infos.append(_function_info(tool))
    return infos, mcps


def build_snapshot(runner: Optional[object]) -> TeamSnapshot:
    """A `TeamSnapshot` of a live `Team` (with members) or a bare `Agent` (no members)."""
    if runner is None:
        return TeamSnapshot(name="", lead_model="", is_team=False)

    name = _text(getattr(runner, "name", None)) or type(runner).__name__
    lead_model = _text(getattr(getattr(runner, "model", None), "id", None))
    team_tools, mcps = _expand_tools(getattr(runner, "tools", None), name)

    members_attr = getattr(runner, "members", None)
    if not members_attr:  # a plain single-agent runner
        return TeamSnapshot(
            name=name,
            lead_model=lead_model,
            is_team=False,
            team_tools=team_tools,
            mcp_servers=mcps,
        )

    members: list[MemberInfo] = []
    for member in members_attr:
        member_name = _text(getattr(member, "name", None)) or type(member).__name__
        tools, member_mcps = _expand_tools(getattr(member, "tools", None), member_name)
        mcps.extend(member_mcps)
        members.append(
            MemberInfo(
                name=member_name,
                role=_text(getattr(member, "role", None)),
                model=_text(getattr(getattr(member, "model", None), "id", None)),
                tools=tools,
            )
        )

    return TeamSnapshot(
        name=name,
        lead_model=lead_model,
        is_team=True,
        members=members,
        team_tools=team_tools,
        mcp_servers=mcps,
    )
