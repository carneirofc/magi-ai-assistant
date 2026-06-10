"""Single-agent builder.

`build_agent` is the generic, fully-injectable primitive for the non-team path:
pass the result as the `runner` of a `ConversationService` instead of a team.
Every argument defaults from config but can be overridden, so callers opt into
exactly what they need. Memory (`enable_user_memories`) and history both
require a `db`.
"""

from collections.abc import Sequence

from agno.agent import Agent
from agno.db.base import BaseDb
from agno.models.base import Model
from agno.utils.log import log_info

from agent.model import build_member_model
from agent.tools import enabled_tools
from core.config import config


def build_agent(
    *,
    model: Model | None = None,
    system_message: str | None = None,
    tools: Sequence | None = None,
    db: BaseDb | None = None,
    add_history_to_context: bool = False,
    num_history_runs: int = 10,
    enable_user_memories: bool = False,
    markdown: bool = True,
) -> Agent:
    resolved_tools = enabled_tools(tools)
    resolved_model = model or build_member_model()
    resolved_system = system_message or config.system_prompt
    tool_names = [
        getattr(t, "name", getattr(t, "__name__", type(t).__name__))
        for t in resolved_tools
    ]
    log_info(
        f"building agent: system_prompt={len(resolved_system)} chars, tools={tool_names or 'none'}, "
        f"db={'on' if db else 'off'}, history={add_history_to_context} (n={num_history_runs}), "
        f"memory={enable_user_memories}"
    )
    return Agent(
        model=resolved_model,
        system_message=resolved_system,
        tools=resolved_tools,
        db=db,
        add_history_to_context=add_history_to_context,
        num_history_runs=num_history_runs,
        enable_user_memories=enable_user_memories,
        markdown=markdown,
        telemetry=False,
    )
