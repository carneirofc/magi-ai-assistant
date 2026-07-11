"""Memory skills — how the lead deliberately *reads* its own memory.

Durable memory is no longer written by the lead inline: the post-turn curator
(magi/agent/curator.py) owns it, rewriting the long-term profile and recording episodes
off the reply path. So the lead keeps only read tools here — and it rarely needs
even these, since `MemoryManager.build_context` already injects the current
profile, episodes, and short-term window into every run. They exist for explicit,
deeper recall.

`build_memory_tools(memory)` binds the tools to an injected `MemoryManager` (no
globals): the channel sets the active user/session scope on that manager before
each run, so the model calls these with no arguments.
"""

from typing import Annotated

from agno.tools import tool
from pydantic import BaseModel, Field

from magi.agent.tools.outputs import ToolOutput, ok
from magi.core.memory import MemoryManager


class LongTermMemoryData(BaseModel):
    memory: str = Field(description="Recalled long-term memory text.")


class EpisodicMemoryData(BaseModel):
    episodes: str = Field(description="Recalled episodic summaries.")
    limit: int = Field(description="Maximum number of summaries requested.")


class ConversationSearchData(BaseModel):
    query: str = Field(description="What was searched for.")
    results: str = Field(description="Matching lines from past conversations, with their source.")


def build_memory_tools(memory: MemoryManager) -> list:
    """Return the (read-only) memory tool set bound to `memory` (dependency-injected)."""

    @tool(
        description="Recall the durable profile remembered about the current user.",
        instructions="Use when prior user preferences or durable facts may affect the answer. Takes no arguments.",
        show_result=True,
    )
    def recall_memory() -> ToolOutput[LongTermMemoryData]:
        """Return the durable profile you remember about the current user (long-term memory)."""
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
        description=(
            "Search the current user's PAST conversations (old session transcripts, "
            "session summaries, episodes) for something said before."
        ),
        instructions=(
            "Use when the user refers to an earlier conversation ('like I said the "
            "other day', 'that link you sent me', 'what did we decide about X'). "
            "Pass the key phrase to look for; results cite where each line came "
            "from. The current conversation is already in your context — this is "
            "for previous ones."
        ),
        show_result=True,
    )
    def recall_conversation(
        query: Annotated[
            str,
            Field(min_length=2, description="The phrase or topic to look for in past conversations."),
        ],
        limit: Annotated[
            int,
            Field(default=8, ge=1, le=20, description="Maximum matches to return."),
        ] = 8,
    ) -> ToolOutput[ConversationSearchData]:
        """Find what was said in previous conversations with the current user."""
        results = memory.search_history(query, limit)
        return ok(
            "Searched past conversations.",
            ConversationSearchData(query=query, results=results),
        )

    return [recall_memory, recall_episodes, recall_conversation]
