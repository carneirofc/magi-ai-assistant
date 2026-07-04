"""Channel-agnostic service assembly.

Every channel (Discord, HTTP API, future ones) serves the same brain: the
summarizers, memory, team and `ConversationService` are wired identically and
only the channel-specific pieces differ — the output guidance prompt and,
optionally, which team members make sense there. This module is that shared
wiring; each channel's composition root calls it and then adds its own transport
(a discord.Client, a FastAPI app, ...).

Wiring order:

    summarizers (gated by config) -> memory -> team(memory, members) ->
    ConversationService(team, memory, channel_guidance)
"""

from collections.abc import Callable, Sequence
from typing import Optional

from agno.agent import Agent
from agno.db.base import BaseDb
from agno.models.base import Model
from agno.utils.log import log_info

from magi.agent.team import build_team
from magi.core.config import config
from magi.core.conversation import ConversationService
from magi.core.knowledge import build_knowledge_from_config
from magi.core.memory import build_memory_from_config


def build_conversation_service(
    *,
    channel_guidance: str,
    db: Optional[BaseDb] = None,
    member_builders: Optional[Sequence[Callable[[Model], Agent]]] = None,
) -> ConversationService:
    """Assemble the full conversation stack behind one channel-neutral service."""
    config.log_settings()

    # The session summarizer needs a model, so the agent layer builds it; magi/core/memory
    # stays model-free and receives it as an injected callable. Gated by config.
    session_fn = None
    if config.session_summary:
        from magi.agent.summarizer import build_session_summarizer

        session_fn = build_session_summarizer()
        log_info(f"memory: session summary ENABLED (every {config.summarize_every} turns)")
    else:
        log_info("memory: session summary DISABLED")

    # The curator owns durable memory when on (it supersedes the long-term
    # summarizer and the lead's write tools). Needs a model, so the agent layer
    # builds it; magi/core/memory receives it as an injected callable.
    curate_fn = None
    if config.memory_curation:
        from magi.agent.curator import build_memory_curator

        curate_fn = build_memory_curator()
        log_info("memory: curation ENABLED (post-turn durable-memory pass)")
    else:
        log_info("memory: curation DISABLED")

    memory = build_memory_from_config(
        summarize_session_fn=session_fn,
        curate_fn=curate_fn,
    )
    # The knowledge RAG store (None when the feature is off) is built once here and
    # injected into both consumers: the team (its search tool) and the conversation
    # service (context auto-injection). One instance, one connection lifecycle.
    knowledge = build_knowledge_from_config()
    team = build_team(memory, db, member_builders, knowledge=knowledge)
    return ConversationService(
        runner=team,
        memory=memory,
        channel_guidance=channel_guidance,
        # The lead's context window, so replies can report how full it is.
        context_window=config.lead_num_ctx,
        # Surface the top-k most relevant corpus chunks for each message up front
        # (no-op unless knowledge is on and knowledge_context_top_k > 0).
        knowledge=knowledge,
        knowledge_top_k=config.knowledge_context_top_k,
    )
