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

from typing import TYPE_CHECKING, Any, Optional

from agno.utils.log import log_info, log_warning

from magi.core.config import config
from magi.core.prompts import load_prompt

if TYPE_CHECKING:
    from agno.agent import Agent
    from agno.models.base import Model
    from agno.tools.mcp import MCPTools

# Fields a spec may carry; anything else is ignored (forward compatibility).
_DEFAULT_TIMEOUT_S = 30


def effective_mcp_specs() -> list[dict]:
    """The enabled server specs: `config.mcp_servers` merged (by name) with the
    operator settings file's `mcp` section — operator entries win field-wise,
    so an operator can tweak or disable a code-declared server."""
    specs: dict[str, dict] = {}
    for entry in config.mcp_servers:
        name = str(entry.get("name") or "").strip()
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
        name = str(entry.get("name") or "").strip()
        if name:
            specs[name] = {**specs.get(name, {}), **entry}
    return [s for s in specs.values() if s.get("enabled", True)]


def build_mcp_toolkit(spec: dict) -> "MCPTools":
    """One agno MCP toolkit from a spec dict. Raises on a malformed spec or a
    missing `mcp` extra — callers catch and skip (see `_guarded`)."""
    try:
        from agno.tools.mcp import MCPTools
        from agno.tools.mcp.params import SSEClientParams, StreamableHTTPClientParams
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise RuntimeError(
            "config.mcp_servers needs the optional 'mcp' dependency "
            "(`uv sync --extra mcp`)."
        ) from exc

    name = str(spec.get("name") or "").strip()
    transport = str(spec.get("transport") or "streamable-http")
    timeout = int(spec.get("timeout_seconds") or _DEFAULT_TIMEOUT_S)
    headers: Optional[dict[str, Any]] = spec.get("headers") or None
    allow = [str(t) for t in spec.get("tool_allowlist") or []] or None
    show = [str(t) for t in spec.get("show_result_tools") or []]

    common: dict[str, Any] = {
        "timeout_seconds": timeout,
        "include_tools": allow,
        "show_result_tools": show,
        "tool_name_prefix": None,
    }
    if transport == "stdio":
        command = str(spec.get("command") or "").strip()
        if not command:
            raise ValueError(f"mcp server {name!r}: stdio transport needs a 'command'")
        return MCPTools(command=command, env=spec.get("env") or None, transport="stdio", **common)

    url = str(spec.get("url") or "").strip()
    if not url:
        raise ValueError(f"mcp server {name!r}: transport {transport!r} needs a 'url'")
    if transport == "sse":
        params: Any = SSEClientParams(url=url, headers=headers)
    else:
        params = StreamableHTTPClientParams(url=url, headers=headers)
    return MCPTools(server_params=params, transport=transport, **common)


def _guarded(spec: dict) -> Optional["MCPTools"]:
    name = spec.get("name", "?")
    try:
        return build_mcp_toolkit(spec)
    except Exception as exc:  # noqa: BLE001 — one bad server must not brick the boot.
        log_warning(f"mcp: skipping server {name!r}: {type(exc).__name__}: {exc}")
        return None


def _default_member_role(name: str, spec: dict) -> str:
    """A serviceable generated role when the spec supplies none: the base MCP
    member contract (prompts/team/mcp_member.md) with the server named."""
    template = load_prompt("team/mcp_member.md")
    return template.replace("{name}", name).replace(
        "{description}", str(spec.get("description") or "").strip()
    )


def build_mcp_lead_toolkits() -> list:
    """Toolkits for every enabled `attach: "lead"` server — attached to the
    team itself, so the lead calls them directly (no delegation hop)."""
    toolkits = []
    for spec in effective_mcp_specs():
        if spec.get("attach", "member") != "lead":
            continue
        toolkit = _guarded(spec)
        if toolkit is not None:
            toolkits.append(toolkit)
            log_info(f"mcp: lead toolkit '{spec.get('name')}' wired")
    return toolkits


def build_mcp_members(model: "Model") -> list["Agent"]:
    """A generated specialist per enabled `attach: "member"` server. The
    member's name is `<name>-agent`; its role is the spec's `role` (or a
    generic MCP-member contract), so the lead can route to it like any
    hand-written specialist."""
    from agno.agent import Agent

    members: list[Agent] = []
    for spec in effective_mcp_specs():
        if spec.get("attach", "member") != "member":
            continue
        toolkit = _guarded(spec)
        if toolkit is None:
            continue
        name = str(spec.get("name")).strip()
        role = str(spec.get("role") or "").strip() or _default_member_role(name, spec)
        members.append(Agent(name=f"{name}-agent", role=role, model=model, tools=[toolkit]))
        log_info(f"mcp: member '{name}-agent' wired")
    return members
