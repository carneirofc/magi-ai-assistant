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

    # Summarizers need a model, so the agent layer builds them; core/memory stays
    # model-free and receives them as injected callables. Each is gated by config.
    session_fn = None
    long_term_fn = None
    if config.session_summary or config.long_term_summary:
        from agent.summarizer import build_long_term_summarizer, build_session_summarizer

        if config.session_summary:
            session_fn = build_session_summarizer()
            log_info(f"memory: session summary ENABLED (every {config.summarize_every} turns)")
        if config.long_term_summary:
            long_term_fn = build_long_term_summarizer()
            log_info(
                f"memory: long-term summary ENABLED (every {config.long_term_summarize_every} facts, "
                f"+{config.long_term_recent_raw} recent raw)"
            )
    else:
        log_info("memory: summarization DISABLED")

    memory = build_memory_from_config(
        summarize_session_fn=session_fn,
        summarize_long_term_fn=long_term_fn,
    )
    team = build_team(memory, db, member_builders)
    return ConversationService(
        runner=team,
        memory=memory,
        channel_guidance=channel_guidance,
    )
