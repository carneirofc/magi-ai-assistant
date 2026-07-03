"""Researcher — the team's fact-lookup specialist.

The lead routes knowledge/research questions here. Its `role` (from
prompts/team/researcher.md) tells the lead when to pick it; tools come from the
shared registry, gated by model capability.
"""

from agno.agent import Agent
from agno.models.base import Model

from magi.agent.tools import enabled_tools
from magi.core.context import AgentContext
from magi.core.prompts import load_prompt


def build_researcher(ctx: AgentContext, model: Model) -> Agent:
    return Agent(
        name="Researcher",
        role=load_prompt("team/researcher.md"),
        model=model,
        tools=enabled_tools(ctx.config),
    )
