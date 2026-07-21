"""Skills / actions the agent can call, plus the single tool-gating point.

To add a capability inside the engine tree:
  1. Write a plain function in its own module under `magi/agent/tools/`.
  2. Decorate with @tool(show_result=True) (the docstring tells the model when to call it).
  3. Import it here and add it to DEFAULT_TOOLS.

A persona overlay does neither: it calls `register_tool(fn)` (member default
set) or `register_lead_toolkit(builder)` (lead-level, memory-injected) at its
entrypoint, before `build_team()` — the tool twin of
`magi.agent.members.register_member`.

`enabled_tools` is the single place agents and members resolve their tools, so
the default set is never duplicated across builders.
"""

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from agno.utils.log import log_warning

from magi.agent.tools.http import HTTP_TOOLS
from magi.agent.tools.time import get_current_time

if TYPE_CHECKING:
    from magi.core.memory import MemoryManager

# Tools every agent gets by default. Extend this list as you add skills.
# The deliberate memory tools are NOT here: they're bound to an injected
# MemoryManager (see magi.agent.tools.memory.build_memory_tools) and attached to the
# lead in magi/agent/team.py, so memory writes stay centralized — no globals.
DEFAULT_TOOLS: list = [get_current_time, *HTTP_TOOLS]

# Lead-level toolkit builders registered from outside the engine tree. Each
# builder receives the injected MemoryManager (the same convention as the
# engine's own build_*_tools) and returns the tools to splice into the lead.
LEAD_TOOLKIT_BUILDERS: list[Callable[["MemoryManager"], Sequence]] = []


def register_tool(fn):
    """Append a tool to the member default set; return it (usable as a decorator).

    Call at the entrypoint, before `build_team()` — the set flows to members
    through `enabled_tools()`. The list is mutated in place, so a persona
    extends it without editing the public tree. Idempotent: re-registering the
    same tool is a no-op, so a re-imported entrypoint doesn't duplicate tools.
    """
    if fn not in DEFAULT_TOOLS:
        DEFAULT_TOOLS.append(fn)
    return fn


def register_lead_toolkit(
    builder: Callable[["MemoryManager"], Sequence],
) -> Callable[["MemoryManager"], Sequence]:
    """Append a lead toolkit builder; return it (usable as a decorator).

    Call at the entrypoint, before `build_team()` reads the registry. The
    builder is invoked at team build with the injected MemoryManager and its
    tools land on the lead — so persona tools get the same dependency
    injection as the engine's own (no globals). Idempotent like
    `register_member`.
    """
    if builder not in LEAD_TOOLKIT_BUILDERS:
        LEAD_TOOLKIT_BUILDERS.append(builder)
    return builder


def registered_lead_tools(memory: "MemoryManager") -> list:
    """Flatten every registered lead toolkit into one tool list.

    A raising builder is skipped with a warning — a broken persona toolkit
    must not abort team build, matching how the other optional tool sources
    degrade.
    """
    tools: list = []
    for builder in LEAD_TOOLKIT_BUILDERS:
        try:
            tools.extend(builder(memory))
        except Exception as exc:  # noqa: BLE001 — degrade, don't abort startup.
            log_warning(
                f"lead toolkit {getattr(builder, '__name__', builder)!r} skipped "
                f"({type(exc).__name__}: {exc})"
            )
    return tools


def enabled_tools(tools: Sequence | None = None) -> list:
    """Resolve the tools to attach to an magi/agent/member.

    `None` means the default set — plus any active skill's member tools (late
    import: skills sit above this module). Pass an explicit list to override
    everything.
    """
    if tools is None:
        from magi.agent.skills import skill_member_tools

        return [*DEFAULT_TOOLS, *skill_member_tools()]
    return list(tools)
