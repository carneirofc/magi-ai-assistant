"""Memory skills — how the model deliberately reads and writes its own memory.

`build_memory_tools(memory)` binds the model-facing tools to an injected
`MemoryManager` (no globals): the channel sets the active user/session scope on
that manager before each run, so the model calls these with content only — no ids
to pass. Docstrings are the model's contract: they tell it WHEN to keep something.
"""

from typing import Annotated

from agno.tools import tool
from pydantic import BaseModel, Field

from agent.tools.outputs import ToolOutput, ok
from core.memory import MemoryManager


class FactData(BaseModel):
    fact: str = Field(description="The durable fact that was stored.")


class EpisodeData(BaseModel):
    summary: str = Field(description="The episodic summary that was stored.")


class LongTermMemoryData(BaseModel):
    memory: str = Field(description="Recalled long-term memory text.")


class EpisodicMemoryData(BaseModel):
    episodes: str = Field(description="Recalled episodic summaries.")
    limit: int = Field(description="Maximum number of summaries requested.")


class PersonaAdjustmentData(BaseModel):
    adjustment: str = Field(description="The persona adjustment that was stored.")


def build_memory_tools(memory: MemoryManager) -> list:
    """Return the memory tool set bound to `memory` (dependency-injected)."""

    @tool(
        description="Save one durable long-term fact about the current user.",
        instructions=(
            "Use only for stable, reusable facts such as preferences, name, projects, "
            "or recurring needs. Write the fact as a standalone statement."
        ),
        show_result=True,
    )
    def remember(
        fact: Annotated[
            str,
            Field(
                min_length=3,
                description="Standalone durable fact to remember about the current user.",
            ),
        ],
    ) -> ToolOutput[FactData]:
        """Save a durable fact about the current user to long-term memory.

        Use for stable, reusable facts (preferences, name, projects, recurring
        needs) — not passing chatter. Phrase it as a standalone statement.
        """
        return ok(memory.remember(fact), FactData(fact=fact))

    @tool(
        description="Record a one-line episodic summary of the current interaction.",
        instructions=(
            "Use at a natural close or after a notable outcome. Summarize what the "
            "user wanted and how the interaction went."
        ),
        show_result=True,
    )
    def record_episode(
        summary: Annotated[
            str,
            Field(
                min_length=5,
                description="One-line summary of this interaction and its outcome.",
            ),
        ],
    ) -> ToolOutput[EpisodeData]:
        """Log a one-line summary of what happened in this interaction (episodic memory).

        Use at a natural close, or after something notable, to record the gist of
        an episode you may want to recall later: what the user wanted and how it went.
        """
        return ok(memory.record_episode(summary), EpisodeData(summary=summary))

    @tool(
        description="Recall all long-term facts remembered about the current user.",
        instructions="Use when prior user preferences or durable facts may affect the answer. Takes no arguments.",
        show_result=True,
    )
    def recall_memory() -> ToolOutput[LongTermMemoryData]:
        """Return everything you remember about the current user (long-term facts)."""
        text = memory.recall_long_term()
        return ok("Recalled long-term memory.", LongTermMemoryData(memory=text))

    @tool(
        description="Recall recent episodic memory summaries for the current user.",
        instructions="Use to inspect recent interaction history. The optional limit defaults to 5.",
        show_result=True,
    )
    def recall_episodes(
        limit: Annotated[
            int,
            Field(
                default=5,
                ge=1,
                le=20,
                description="Maximum number of recent episodic summaries to return.",
            ),
        ] = 5,
    ) -> ToolOutput[EpisodicMemoryData]:
        """Return summaries of the most recent past episodes with the current user."""
        text = memory.recall_episodes(limit)
        return ok("Recalled episodic memory.", EpisodicMemoryData(episodes=text, limit=limit))

    @tool(
        description="Record a lasting adjustment to the assistant's persona or behavior.",
        instructions=(
            "Use only for deliberate general behavior rules learned from an interaction, "
            "not one-off reactions or user-specific facts."
        ),
        show_result=True,
    )
    def evolve_persona(
        adjustment: Annotated[
            str,
            Field(
                min_length=5,
                description="General lasting behavior or persona rule to apply going forward.",
            ),
        ],
    ) -> ToolOutput[PersonaAdjustmentData]:
        """Record a lasting adjustment to your own personality or behavior.

        Use when an interaction teaches you how to act better going forward (tone,
        habits, what to avoid). This evolves your persona across all users — keep it
        a deliberate, general rule, not a one-off reaction.
        """
        return ok(memory.evolve_persona(adjustment), PersonaAdjustmentData(adjustment=adjustment))

    return [remember, record_episode, recall_memory, recall_episodes, evolve_persona]
