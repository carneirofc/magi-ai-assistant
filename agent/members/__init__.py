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
from agent.members.litellm import build_litellm_specialist
from agent.members.ollama import build_ollama_specialist
from agent.members.researcher import build_researcher


def build_discord_agent(model: Model) -> Agent:
    """Discord REST specialist — UNWIRED by default (not in MEMBER_BUILDERS).

    Thread/channel routing is the channel layer's job: clients/mydiscord.py owns
    thread creation, the confirmation prompt and session routing. This member only
    ever sees the user's name/id/url in context — never the guild/channel/thread
    ids — so when the lead delegated "start a new thread" here, agno's DiscordTools
    filled the ids with the *user id* and every REST call 404'd. Leaving it out of
    the roster stops the lead from routing Discord plumbing it cannot do.

    Wire it back only if you also feed it the real guild/channel/thread ids and a
    bot token, and scope it to actions the channel layer does NOT already own.
    """
    from agno.tools.discord import DiscordTools

    return Agent(
        name="Discord Bot",
        role="Discord specialist, the team's expert on all things Discord.",
        model=model,
        tools=[DiscordTools()],
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


# Ordered set of members the team is built from. `build_discord_agent` and
# `build_docker` are intentionally omitted — opt-in only (see their docstrings).
# The Discord member in particular MUST stay out: thread/channel routing is the
# channel layer's job, and the member has no conversation metadata so its
# DiscordTools 404 on hallucinated ids.
MEMBER_BUILDERS: list[Callable[[Model], Agent]] = [
    build_assistant,
    build_researcher,
    build_ollama_specialist,
    build_litellm_specialist,
]
