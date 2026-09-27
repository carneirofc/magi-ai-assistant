"""`delegate_task` — hand a self-contained subtask to a fresh, isolated agent.

The subagent is built per call from the member model with the member default
tools (`enabled_tools()`): no memory tools, no `delegate_task` (depth 1), and
no team members. It gets its own tool-call cap and a hard timeout, and the
shared tool hook logs its calls. Only its final answer comes back, so a long
research/fetch/transform job doesn't flood the lead's context.
"""

import asyncio
from collections.abc import Callable, Sequence
from typing import Annotated

from agno.agent import Agent
from agno.models.base import Model
from agno.tools import Toolkit, tool
from agno.tools.function import Function
from agno.utils.message import get_text_from_message
from pydantic import BaseModel, Field

from magi.agent.hooks import tool_call_hook
from magi.agent.tools.outputs import ToolOutput, fail, ok

_INSTRUCTIONS = (
    "You are a focused sub-agent. Complete exactly the task you are given, using "
    "your tools when they help. You cannot ask follow-up questions: make "
    "reasonable assumptions and state them. Reply with the result only — "
    "concise, complete, and ready for another assistant to use."
)


class DelegateData(BaseModel):
    result: str = Field(description="The sub-agent's final answer.")


def build_delegate_tools(
    model_factory: Callable[[], Model],
    tools_factory: Callable[[], Sequence[Toolkit | Callable[..., object] | Function]],
    *,
    timeout_seconds: float,
    tool_call_limit: int,
) -> list[Function]:
    @tool(
        description="Delegate a self-contained subtask to an isolated helper agent.",
        instructions=(
            "Use for a well-defined job that needs several steps or tool calls but "
            "whose details you don't need to see — e.g. fetch and summarize a few "
            "pages, extract data from a long document, draft something to a spec. "
            "Put EVERYTHING the helper needs in `goal` and `context` (it cannot see "
            "this conversation or the user's memory). Prefer a team member when one "
            "specializes in the task."
        ),
        show_result=True,
    )
    async def delegate_task(
        goal: Annotated[str, Field(min_length=10, description="What to accomplish.")],
        context: Annotated[
            str, Field(default="", description="Facts, inputs, and constraints it needs.")
        ] = "",
    ) -> ToolOutput[DelegateData]:
        agent = Agent(
            name="subagent",
            model=model_factory(),
            tools=tools_factory(),
            instructions=_INSTRUCTIONS,
            tool_call_limit=tool_call_limit,
            tool_hooks=[tool_call_hook],
            telemetry=False,
        )
        prompt = f"Task: {goal}\n\nContext:\n{context}" if context.strip() else f"Task: {goal}"
        try:
            run = await asyncio.wait_for(agent.arun(input=prompt), timeout=timeout_seconds)
        except TimeoutError:
            return fail(f"The helper did not finish within {timeout_seconds:.0f}s.")
        except Exception as exc:  # noqa: BLE001 — a failed helper is a tool result.
            return fail(f"The helper failed: {type(exc).__name__}: {exc}")
        text = get_text_from_message(run.content) if run.content else ""
        if run.status == "ERROR" or not text.strip():
            return fail(f"The helper returned no usable answer ({run.status}).")
        return ok("Helper finished.", DelegateData(result=text))

    return [delegate_task]
