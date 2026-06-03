"""Skills / actions the agent can call, plus the single tool-gating point.

To add a capability:
  1. Write a plain function in its own module under `agent/tools/`.
  2. Decorate with @tool (the docstring tells the model when to call it).
  3. Import it here and add it to DEFAULT_TOOLS.

`enabled_tools` is the one place that honors the model's tool-calling capability
(config.tools_enabled) — both single agents and team members resolve their tools
through it, so the gating logic is never duplicated.
"""

from collections.abc import Sequence

from agent.tools.time import get_current_time
from core.config import config

# Tools every agent gets by default. Extend this list as you add skills.
DEFAULT_TOOLS = [get_current_time]


def enabled_tools(tools: Sequence | None = None) -> list:
    """Resolve the tools to attach to an agent/member.

    `None` means the default set, gated by model capability (some local Ollama
    models reject tools with HTTP 400 — see config.tools_enabled). Pass an
    explicit list to override the default entirely.
    """
    if tools is None:
        tools = DEFAULT_TOOLS
    return list(tools)
