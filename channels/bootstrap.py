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

from agent.team import build_team
from core.config import config
from core.conversation import ConversationService
from core.memory import build_memory_from_config


def build_conversation_service(
    *,
    channel_guidance: str,
    db: Optional[BaseDb] = None,
    member_builders: Optional[Sequence[Callable[[Model], Agent]]] = None,
) -> ConversationService:
    """Assemble the full conversation stack behind one channel-neutral service."""
    config.log_settings()

    # The session summarizer needs a model, so the agent layer builds it; core/memory
    # stays model-free and receives it as an injected callable. Gated by config.
    session_fn = None
    if config.session_summary:
        from agent.summarizer import build_session_summarizer

        session_fn = build_session_summarizer()
        log_info(f"memory: session summary ENABLED (every {config.summarize_every} turns)")
    else:
        log_info("memory: session summary DISABLED")

    # The curator owns durable memory when on (it supersedes the long-term
    # summarizer and the lead's write tools). Needs a model, so the agent layer
    # builds it; core/memory receives it as an injected callable.
    curate_fn = None
    if config.memory_curation:
        from agent.curator import build_memory_curator

        curate_fn = build_memory_curator()
        log_info("memory: curation ENABLED (post-turn durable-memory pass)")
    else:
        log_info("memory: curation DISABLED")

    memory = build_memory_from_config(
        summarize_session_fn=session_fn,
        curate_fn=curate_fn,
    )
    team = build_team(memory, db, member_builders)
    return ConversationService(
        runner=team,
        memory=memory,
        channel_guidance=channel_guidance,
    )
