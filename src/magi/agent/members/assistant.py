"""General Assistant — the team's everyday-conversation specialist.

The lead routes general chat here. Its `role` (loaded from prompts/team/assistant.md)
tells the lead when to pick it; tools come from the shared registry, gated by
model capability.
"""

from agno.agent import Agent
from agno.models.base import Model

from magi.agent.tools import enabled_tools
from magi.core.context import AgentContext
from magi.core.prompts import load_prompt


def build_assistant(ctx: AgentContext, model: Model) -> Agent:
    return Agent(
        name="Assistant",
        role=load_prompt("team/assistant.md"),
        model=model,
        tools=enabled_tools(ctx.config),
    )
