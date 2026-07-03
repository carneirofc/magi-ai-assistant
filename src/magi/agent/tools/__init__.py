"""Skills / actions the agent can call, plus the single tool-gating point.

To add a capability:
  1. Write a plain function in its own module under `magi/agent/tools/`.
  2. Decorate with @tool(show_result=True) (the docstring tells the model when to call it).
  3. If it needs config, expose a `build_<x>_tools(config)` builder; otherwise add
     the plain tool to `default_tools()` below.

`enabled_tools` is the single place agents and members resolve their tools, so
the default set is never duplicated across builders. It takes the `config` the
config-bound tools (e.g. the HTTP tools' SSRF guard) close over — there is no
module global to read.
"""

from collections.abc import Sequence

from magi.agent.tools.http import build_http_tools
from magi.agent.tools.time import get_current_time
from magi.core.config import Config


def default_tools(config: Config) -> list:
    """Tools every agent gets by default, bound to `config`. Extend as you add skills.

    The deliberate memory tools are NOT here: they're bound to an injected
    MemoryManager (see magi.agent.tools.memory.build_memory_tools) and attached to
    the lead in magi/agent/team.py, so memory writes stay centralized — no globals.
    """
    return [get_current_time, *build_http_tools(config)]


def enabled_tools(config: Config, tools: Sequence | None = None) -> list:
    """Resolve the tools to attach to an agent/member.

    `None` means the default set (bound to `config`); pass an explicit list to
    override it entirely.
    """
    if tools is None:
        return default_tools(config)
    return list(tools)
