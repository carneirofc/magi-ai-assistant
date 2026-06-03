"""Single-agent builders.

`build_agent` is the generic, fully-injectable primitive — every argument
defaults from config but can be overridden. The named presets below
(stateless / discord) just pick sensible defaults for each channel by calling
`build_agent`.
"""

from collections.abc import Sequence

from agno.agent import Agent
from agno.db.base import BaseDb
from agno.models.base import Model
from agno.utils.log import log_info

from agent.model import build_model
from agent.tools import enabled_tools
from core.config import config
from core.db import get_db


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
    """Generic, fully-injectable agent builder.

    Every arg defaults from config / off, so callers opt into exactly what they
    need. Memory (`enable_user_memories`) and history both require a `db`.
    """
    resolved_tools = enabled_tools(tools)
    resolved_model = model or build_model()
    resolved_system = system_message or config.system_prompt
    tool_names = [
        getattr(t, "name", getattr(t, "__name__", type(t).__name__)) for t in resolved_tools
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


def build_stateless_agent(**overrides) -> Agent:
    """OpenWebUI preset: caller supplies full history each request, so no db."""
    return build_agent(**overrides)


def build_discord_agent(db: BaseDb | None = None, **overrides) -> Agent:
    """Discord preset: agent owns the session, so persist history + user memory.

    Long-term user memory needs tool calling; it's auto-disabled when the model
    lacks tool support (config.tools_enabled). Short-term history works regardless.
    """
    log_info("preset: building discord agent (persisted history + user memory)")
    return build_agent(
        db=db or get_db(),
        add_history_to_context=True,
        enable_user_memories=config.tools_enabled,
        **overrides,
    )
