"""Memory skills — how the model deliberately reads and writes its own memory.

`build_memory_tools(memory)` binds the model-facing tools to an injected
`MemoryManager` (no globals): the channel sets the active user/session scope on
that manager before each run, so the model calls these with content only — no ids
to pass. Docstrings are the model's contract: they tell it WHEN to keep something.
"""

from agno.tools import tool

from core.memory import MemoryManager


def build_memory_tools(memory: MemoryManager) -> list:
    """Return the memory tool set bound to `memory` (dependency-injected)."""

    @tool
    def remember(fact: str) -> str:
        """Save a durable fact about the current user to long-term memory.

        Use for stable, reusable facts (preferences, name, projects, recurring
        needs) — not passing chatter. Phrase it as a standalone statement.
        """
        return memory.remember(fact)

    @tool
    def record_episode(summary: str) -> str:
        """Log a one-line summary of what happened in this interaction (episodic memory).

        Use at a natural close, or after something notable, to record the gist of
        an episode you may want to recall later: what the user wanted and how it went.
        """
        return memory.record_episode(summary)

    @tool
    def recall_memory() -> str:
        """Return everything you remember about the current user (long-term facts)."""
        return memory.recall_long_term()

    @tool
    def recall_episodes(limit: int = 5) -> str:
        """Return summaries of the most recent past episodes with the current user."""
        return memory.recall_episodes(limit)

    @tool
    def evolve_persona(adjustment: str) -> str:
        """Record a lasting adjustment to your own personality or behavior.

        Use when an interaction teaches you how to act better going forward (tone,
        habits, what to avoid). This evolves your persona across all users — keep it
        a deliberate, general rule, not a one-off reaction.
        """
        return memory.evolve_persona(adjustment)

    return [remember, record_episode, recall_memory, recall_episodes, evolve_persona]
