"""Seanime specialist — the user's local anime media server.

The lead routes anime-library work here: what's in the library, watch progress,
missing episodes, the airing schedule, AniList lookups via Seanime, and marking
episodes watched. Its `role` (prompts/team/seanime.md) tells the lead when to
pick it; its tools call the Seanime HTTP API at `config.seanime_base_url`.
"""

from agno.agent import Agent
from agno.models.base import Model

from agent.tools.seanime import SEANIME_TOOLS
from core.prompts import load_prompt


def build_seanime_specialist(model: Model) -> Agent:
    return Agent(
        name="Seanime",
        role=load_prompt("team/seanime.md"),
        model=model,
        tools=SEANIME_TOOLS,
    )
