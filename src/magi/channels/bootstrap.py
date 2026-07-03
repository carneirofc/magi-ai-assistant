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
from magi.core.context import AgentContext
from magi.core.conversation import ConversationService
from magi.core.memory import build_memory_from_config


def build_conversation_service(
    ctx: AgentContext,
    *,
    channel_guidance: str,
    db: Optional[BaseDb] = None,
    member_builders: Optional[Sequence[Callable[[AgentContext, Model], Agent]]] = None,
) -> ConversationService:
    """Assemble the full conversation stack behind one channel-neutral service.

    `ctx` carries the immutable config and the shared services the whole build
    graph reads — nothing here reaches for a process global.
    """
    config = ctx.config
    config.log_settings()

    # The session summarizer needs a model, so the agent layer builds it; magi/core/memory
    # stays model-free and receives it as an injected callable. Gated by config.
    session_fn = None
    if config.session_summary:
        from magi.agent.summarizer import build_session_summarizer

        session_fn = build_session_summarizer(config)
        log_info(f"memory: session summary ENABLED (every {config.summarize_every} turns)")
    else:
        log_info("memory: session summary DISABLED")

    # The curator owns durable memory when on (it supersedes the long-term
    # summarizer and the lead's write tools). Needs a model, so the agent layer
    # builds it; magi/core/memory receives it as an injected callable.
    curate_fn = None
    if config.memory_curation:
        from magi.agent.curator import build_memory_curator

        curate_fn = build_memory_curator(config)
        log_info("memory: curation ENABLED (post-turn durable-memory pass)")
    else:
        log_info("memory: curation DISABLED")

    memory = build_memory_from_config(
        config,
        summarize_session_fn=session_fn,
        curate_fn=curate_fn,
    )
    team = build_team(ctx, memory, db, member_builders)
    return ConversationService(
        runner=team,
        memory=memory,
        channel_guidance=channel_guidance,
    )
