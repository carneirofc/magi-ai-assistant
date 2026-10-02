"""Generic MCP registry — config-driven Model Context Protocol servers.

The hand-wired MCP members (the engine's Seanime-over-MCP surface, a persona's
ComfyUI specialist) proved the pattern: agno's `MCPTools` discovers a server's
tools at connect time, the API channel pre-connects every MCP toolkit found on
the team at startup, and introspection reports connection status. This module
generalizes it: `config.mcp_servers` (see core/config.py for the spec shape)
declares servers as data, and the team builder turns each into either its own
generated specialist member (`attach: "member"`, the default — the server's
tool surface stays behind a role the lead routes to) or a toolkit attached to
the lead itself (`attach: "lead"`).

Operator-added servers live in the operator settings file under an `mcp`
section (admin: GET/PUT /admin/v1/settings/mcp) and MERGE over the code list
by name — an operator can add a server or disable a code-declared one without
editing main.py. The team is assembled at startup, so changes apply on
restart; the admin endpoint says so rather than pretending otherwise.

Failure containment: every builder guards a single spec — a missing `mcp`
extra, a malformed entry, or an unreachable server is a warning and a skipped
server, never a boot failure (connect errors surface later through the API's
lifespan hook + introspection, which already handle MCP toolkits generically).
"""

from typing import TYPE_CHECKING, Literal

from agno.utils.log import log_info, log_warning
from pydantic import BaseModel, ConfigDict, Field

from magi.core.config import config
from magi.core.prompts import load_prompt
from magi.core.types import JsonObject

if TYPE_CHECKING:
    from agno.agent import Agent
    from agno.models.base import Model
    from agno.tools.mcp import MCPTools
    from agno.tools.mcp.params import SSEClientParams, StreamableHTTPClientParams

_DEFAULT_TIMEOUT_S = 30


class McpServerSpec(BaseModel):
    """One MCP server declaration, validated from the merged spec dict.

    Unknown keys are ignored (forward compatibility); a wrong-typed field is a
    `ValidationError`, which `_guarded` turns into a skipped server."""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1)
    transport: Literal["streamable-http", "sse", "stdio"] = "streamable-http"
    url: str | None = None
    command: str | None = None
    headers: dict[str, str] | None = None
    env: dict[str, str] | None = None
    timeout_seconds: int = _DEFAULT_TIMEOUT_S
    enabled: bool = True
    attach: Literal["member", "lead"] = "member"
    role: str | None = None
    description: str | None = None
    tool_allowlist: list[str] = Field(default_factory=list)
    show_result_tools: list[str] = Field(default_factory=list)


def _spec_name(entry: JsonObject) -> str:
    name = entry.get("name")
    return name.strip() if isinstance(name, str) else ""


def effective_mcp_specs() -> list[JsonObject]:
    """The enabled server specs: `config.mcp_servers` merged (by name) with the
    operator settings file's `mcp` section — operator entries win field-wise,
    so an operator can tweak or disable a code-declared server."""
    specs: dict[str, JsonObject] = {}
    for entry in config.mcp_servers:
        name = _spec_name(entry)
        if name:
            specs[name] = dict(entry)
    try:
        from magi.core.memory import operator_settings_store

        store = operator_settings_store()
        operator_entries = store.read_mcp() if store is not None else []
    except Exception as exc:  # noqa: BLE001 — settings must not break team assembly.
        log_warning(f"mcp: could not read operator settings: {type(exc).__name__}: {exc}")
        operator_entries = []
    for entry in operator_entries:
        name = _spec_name(entry)
        if name:
            specs[name] = {**specs.get(name, {}), **entry}
    return [s for s in specs.values() if s.get("enabled", True) is not False]


def build_mcp_toolkit(raw: JsonObject | McpServerSpec) -> MCPTools:
    """One agno MCP toolkit from a spec. Raises on a malformed spec or a
    missing `mcp` extra — callers catch and skip (see `_guarded`)."""
    spec = raw if isinstance(raw, McpServerSpec) else McpServerSpec.model_validate(raw)
    try:
        from agno.tools.mcp import MCPTools
        from agno.tools.mcp.params import SSEClientParams, StreamableHTTPClientParams
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise RuntimeError(
            "config.mcp_servers needs the optional 'mcp' dependency (`uv sync --extra mcp`)."
        ) from exc

    name = spec.name.strip()
    allow = spec.tool_allowlist or None
    if spec.transport == "stdio":
        command = (spec.command or "").strip()
        if not command:
            raise ValueError(f"mcp server {name!r}: stdio transport needs a 'command'")
        return MCPTools(
            command=command,
            env=spec.env or None,
            transport="stdio",
            timeout_seconds=spec.timeout_seconds,
            include_tools=allow,
            show_result_tools=spec.show_result_tools,
            tool_name_prefix=None,
        )

    url = (spec.url or "").strip()
    if not url:
        raise ValueError(f"mcp server {name!r}: transport {spec.transport!r} needs a 'url'")
    if spec.transport == "sse":
        params: SSEClientParams | StreamableHTTPClientParams = SSEClientParams(
            url=url, headers=spec.headers
        )
    else:
        params = StreamableHTTPClientParams(url=url, headers=spec.headers)
    return MCPTools(
        server_params=params,
        transport=spec.transport,
        timeout_seconds=spec.timeout_seconds,
        include_tools=allow,
        show_result_tools=spec.show_result_tools,
        tool_name_prefix=None,
    )


def _guarded(raw: JsonObject) -> tuple[McpServerSpec, MCPTools] | None:
    """Validate and build one server, or None (with a warning) when either fails."""
    name = _spec_name(raw) or "?"
    try:
        spec = McpServerSpec.model_validate(raw)
        return spec, build_mcp_toolkit(spec)
    except Exception as exc:  # noqa: BLE001 — one bad server must not brick the boot.
        log_warning(f"mcp: skipping server {name!r}: {type(exc).__name__}: {exc}")
        return None


def _default_member_role(spec: McpServerSpec) -> str:
    """A serviceable generated role when the spec supplies none: the base MCP
    member contract (prompts/team/mcp_member.md) with the server named."""
    template = load_prompt("team/mcp_member.md")
    return template.replace("{name}", spec.name.strip()).replace(
        "{description}", (spec.description or "").strip()
    )


def build_mcp_lead_toolkits() -> list[MCPTools]:
    """Toolkits for every enabled `attach: "lead"` server — attached to the
    team itself, so the lead calls them directly (no delegation hop)."""
    toolkits: list[MCPTools] = []
    for raw in effective_mcp_specs():
        if raw.get("attach", "member") != "lead":
            continue
        built = _guarded(raw)
        if built is not None:
            spec, toolkit = built
            toolkits.append(toolkit)
            log_info(f"mcp: lead toolkit '{spec.name}' wired")
    return toolkits


def build_mcp_members(model: Model) -> list[Agent]:
    """A generated specialist per enabled `attach: "member"` server. The
    member's name is `<name>-agent`; its role is the spec's `role` (or a
    generic MCP-member contract), so the lead can route to it like any
    hand-written specialist."""
    from agno.agent import Agent

    members: list[Agent] = []
    for raw in effective_mcp_specs():
        if raw.get("attach", "member") != "member":
            continue
        built = _guarded(raw)
        if built is None:
            continue
        spec, toolkit = built
        name = spec.name.strip()
        role = (spec.role or "").strip() or _default_member_role(spec)
        members.append(Agent(name=f"{name}-agent", role=role, model=model, tools=[toolkit]))
        log_info(f"mcp: member '{name}-agent' wired")
    return members
