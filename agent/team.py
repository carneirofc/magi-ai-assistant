"""Specialist team skeleton — multi-agent routing.

A Team has a lead model that reads each member's `role` and routes the message
to the right specialist (or coordinates several). It is a drop-in replacement for
a single Agent: DiscordClient(team=build_team()).

To grow this:
  - Add a builder fn per specialist (give it a sharp `role` + its own tools).
  - Append it to `members`.
  - The lead handles "when/where to call" automatically from the roles.
"""

from agno.agent import Agent
from agno.team import Team

from agent.factory import _build_model
from agent.tools import DEFAULT_TOOLS
from core.config import config
from core.db import db


def _general_assistant() -> Agent:
    return Agent(
        name="Assistant",
        role="Handle general conversation and everyday questions.",
        model=_build_model(),
        tools=DEFAULT_TOOLS,
    )


def _researcher() -> Agent:
    return Agent(
        name="Researcher",
        role="Look up facts and answer knowledge questions precisely.",
        model=_build_model(),
        tools=DEFAULT_TOOLS,
    )


def build_team() -> Team:
    return Team(
        name="ChatbotTeam",
        model=_build_model(),  # lead / router brain
        members=[_general_assistant(), _researcher()],
        instructions=config.system_prompt,
        db=db,
        add_history_to_context=True,
        num_history_runs=10,
        enable_user_memories=True,
        markdown=True,
        telemetry=False,
    )
