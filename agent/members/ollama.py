"""Ollama specialist — the team's expert on the local Ollama server.

The lead routes Ollama infra questions here (installed models, capabilities,
context length, what's loaded). Its `role` (from prompts/team/ollama.md) tells the
lead when to pick it; its tools query the Ollama API directly.
"""

from agno.agent import Agent
from agno.models.base import Model

from agent.tools.ollama import (
    list_ollama_models,
    list_running_ollama_models,
    show_ollama_model,
)
from core.prompts import load_prompt


def build_ollama_specialist(model: Model) -> Agent:
    return Agent(
        name="Ollama",
        role=load_prompt("team/ollama.md"),
        model=model,
        tools=[list_ollama_models, show_ollama_model, list_running_ollama_models],
    )
