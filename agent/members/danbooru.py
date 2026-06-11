"""Prompt Artist — Danbooru-tag prompt specialist for Illustrious models.

The lead routes image-generation prompt work here: building/editing tag lists,
positive & negative prompts, BREAK chunking, and Civitai checkpoint questions
(it knows MatureRitual best). Its `role` (from prompts/team/danbooru.md) tells
the lead when to pick it; its tools query Danbooru and Civitai behind polite
rate limits so the bot never gets 429-banned.
"""

from agno.agent import Agent
from agno.models.base import Model

from agent.tools.danbooru import DANBOORU_TOOLS
from core.prompts import load_prompt


def build_danbooru_specialist(model: Model) -> Agent:
    return Agent(
        name="Prompt Artist",
        role=load_prompt("team/danbooru.md"),
        model=model,
        tools=DANBOORU_TOOLS,
    )
