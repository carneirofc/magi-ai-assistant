"""Time skill.

The docstring is not a comment — the model reads it to decide WHEN to call the
tool and WHAT each argument means. Keep it precise.
"""

from datetime import datetime
from agno.tools import tool
from pydantic import BaseModel, Field

from agent.tools.outputs import ToolOutput, ok


class CurrentTimeData(BaseModel):
    current_time: str = Field(description="Current local date and time in ISO 8601 format.")


@tool(
    description="Return the current local date and time in ISO 8601 format.",
    instructions="Use when the user asks what time, day, or date it is. Takes no arguments.",
    show_result=True,
)
def get_current_time() -> ToolOutput[CurrentTimeData]:
    """Return the current local date and time (ISO 8601).

    Use when the user asks what time, day, or date it is.
    """
    current_time = datetime.now().isoformat(timespec="seconds")
    return ok("Current local date and time.", CurrentTimeData(current_time=current_time))
