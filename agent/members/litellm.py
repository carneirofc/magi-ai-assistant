"""LiteLLM specialist — the team's expert on the LiteLLM proxy.

The lead routes proxy/gateway questions here (served model_names, backend mapping,
health). Its `role` (from prompts/team/litellm.md) tells the lead when to pick it;
its tools query the LiteLLM proxy admin API.
"""

from agno.agent import Agent
from agno.models.base import Model

from agent.tools.litellm import (
    list_litellm_models,
    litellm_health,
    litellm_model_info,
)
from core.prompts import load_prompt


def build_litellm_specialist(model: Model) -> Agent:
    return Agent(
        name="LiteLLM",
        role=load_prompt("team/litellm.md"),
        model=model,
        tools=[list_litellm_models, litellm_model_info, litellm_health],
    )
