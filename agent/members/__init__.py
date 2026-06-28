"""Team member registry — open for extension by a persona repo.

Each specialist exposes a `build_<name>(model)` factory. `MEMBER_BUILDERS` is the
ordered list the team assembles from. The engine ships a small set of neutral
*demo* specialists so it boots and chats out of the box; a private persona (e.g.
`alyssa`) adds its own specialists at startup via `register_member(builder)` —
no edit to this public tree. Builders that aren't in the list (e.g. Docker) stay
available for opt-in wiring.
"""

from collections.abc import Callable

from agno.agent import Agent
from agno.models.base import Model

from agent.members.assistant import build_assistant
from agent.members.researcher import build_researcher
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


# Ordered set of members the team is built from. These are the engine's neutral
# demo specialists; a persona appends its own via `register_member`. `build_docker`
# is intentionally omitted — opt-in only (see its docstring).
MEMBER_BUILDERS: list[Callable[[Model], Agent]] = [
    build_assistant,
    build_researcher,
    build_discord_agent,
]


def register_member(builder: Callable[[Model], Agent]) -> Callable[[Model], Agent]:
    """Append a specialist builder to the team roster; return it (usable as a
    decorator).

    Call at the entrypoint, before `build_team()` reads `MEMBER_BUILDERS`. The
    list is mutated in place, so a persona extends the roster without editing the
    public tree. Idempotent: re-registering the same builder is a no-op, so a
    re-imported entrypoint doesn't duplicate members.
    """
    if builder not in MEMBER_BUILDERS:
        MEMBER_BUILDERS.append(builder)
    return builder
