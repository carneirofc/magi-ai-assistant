"""Team member registry.

Each specialist exposes a `build_<name>(model)` factory. `MEMBER_BUILDERS` is the
ordered list the team assembles from — append a builder here to add a member, no
team code touched. Builders that aren't in the list (e.g. Docker) stay available
for opt-in wiring.
"""

from collections.abc import Callable

from agno.agent import Agent
from agno.models.base import Model

from agent.members.assistant import build_assistant
from agent.members.danbooru import build_danbooru_specialist
from agent.members.litellm import build_litellm_specialist
from agent.members.ollama import build_ollama_specialist
from agent.members.researcher import build_researcher
from agent.members.seanime import build_seanime_specialist
from agent.tools.discord import DISCORD_TOOLS
from core.prompts import load_prompt


def build_discord_agent(model: Model) -> Agent:
    """Discord specialist for actions inside the current live conversation only."""

    return Agent(
        name="Discord Bot",
        role=load_prompt("team/discord.md"),
        model=model,
        tools=DISCORD_TOOLS,
    )


def build_docker(model: Model) -> Agent:
    from agno.tools.docker import DockerTools

    return Agent(
        name="Docker",
        role=(
            "Docker specialist. The lead routes Docker-related questions here: "
            "containers, images, and orchestration."
        ),
        model=model,
        tools=DockerTools(),
    )


# Ordered set of members the team is built from. `build_docker` is intentionally
# omitted — opt-in only (see its docstring).
MEMBER_BUILDERS: list[Callable[[Model], Agent]] = [
    build_assistant,
    build_researcher,
    build_discord_agent,
    build_ollama_specialist,
    build_litellm_specialist,
    build_danbooru_specialist,
    build_seanime_specialist,
]
