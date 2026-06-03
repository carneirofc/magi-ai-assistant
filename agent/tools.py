"""Skills / actions the agent can call.

To add a capability:
  1. Write a plain function.
  2. Decorate with @tool.
  3. Add it to DEFAULT_TOOLS (or pass a custom list when building the agent).

The docstring is not a comment — the model reads it to decide WHEN to call the
tool and WHAT each argument means. Keep it precise.
"""

from datetime import datetime

from agno.tools import tool


@tool
def get_current_time() -> str:
    """Return the current local date and time (ISO 8601).

    Use when the user asks what time, day, or date it is.
    """
    return datetime.now().isoformat(timespec="seconds")


# Tools every agent gets by default. Extend this list as you add skills.
DEFAULT_TOOLS = [get_current_time]
