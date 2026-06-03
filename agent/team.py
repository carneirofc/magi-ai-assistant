"""Specialist team skeleton — multi-agent routing.

A Team has a lead model that reads each member's `role` and routes the message
to the right specialist (or coordinates several). It is a drop-in replacement for
a single Agent: DiscordClient(team=build_team()).

Like build_agent, everything is injectable (model/db) so the team is testable
and reconfigurable. To grow it: add a `_specialist()` builder with a sharp `role`
+ its own tools, then append it to `members`.
"""

from agno.agent import Agent
from agno.db.base import BaseDb
from agno.models.base import Model
from agno.team import Team
from agno.utils.log import log_info

from agent.factory import build_model
from agent.tools import DEFAULT_TOOLS
from core.config import config
from core.db import get_db
from core.prompts import load_prompt

# Honor model tool-calling capability (see config.tools_enabled).
_TOOLS = DEFAULT_TOOLS if config.tools_enabled else []


def _general_assistant(model: Model) -> Agent:
    return Agent(
        name="Assistant",
        role=load_prompt(
            "team/assistant.md", "Handle general conversation and everyday questions."
        ),
        model=model,
        tools=_TOOLS,
    )


def _researcher(model: Model) -> Agent:
    return Agent(
        name="Researcher",
        role=load_prompt(
            "team/researcher.md", "Look up facts and answer knowledge questions precisely."
        ),
        model=model,
        tools=_TOOLS,
    )


def build_team(
    *,
    model: Model | None = None,
    db: BaseDb | None = None,
) -> Team:
    model = model or build_model()
    members = [_general_assistant(model), _researcher(model)]
    lead_instructions = load_prompt("team/lead.md", config.system_prompt)
    log_info(
        f"building team 'ChatbotTeam': members={[m.name for m in members]}, "
        f"lead_prompt={len(lead_instructions)} chars, db={'injected' if db else 'default'}, "
        f"history=True (n=10), memory={config.tools_enabled}"
    )
    return Team(
        name="ChatbotTeam",
        model=model,  # lead / router brain
        members=members,
        instructions=lead_instructions,
        db=db or get_db(),
        add_history_to_context=True,
        num_history_runs=10,
        enable_user_memories=config.tools_enabled,
        markdown=True,
        telemetry=False,
    )
