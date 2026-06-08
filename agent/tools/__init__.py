"""Skills / actions the agent can call, plus the single tool-gating point.

To add a capability:
  1. Write a plain function in its own module under `agent/tools/`.
  2. Decorate with @tool (the docstring tells the model when to call it).
  3. Import it here and add it to DEFAULT_TOOLS.

`enabled_tools` is the single place agents and members resolve their tools, so
the default set is never duplicated across builders.
"""

from collections.abc import Sequence

from agent.tools.time import get_current_time

# Tools every agent gets by default. Extend this list as you add skills.
# The deliberate memory tools are NOT here: they're bound to an injected
# MemoryManager (see agent.tools.memory.build_memory_tools) and attached to the
# lead in agent/team.py, so memory writes stay centralized — no globals.
DEFAULT_TOOLS = [get_current_time]


def enabled_tools(tools: Sequence | None = None) -> list:
    """Resolve the tools to attach to an agent/member.

    `None` means the default set; pass an explicit list to override it entirely.
    """
    if tools is None:
        tools = DEFAULT_TOOLS
    return list(tools)
