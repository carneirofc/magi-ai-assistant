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
from pathlib import Path

from agno.agent import Agent
from agno.db.base import BaseDb
from agno.models.base import Model
from agno.utils.log import log_info

from magi.agent.team import build_team
from magi.core.config import config
from magi.core.conversation import ConversationService
from magi.core.knowledge import build_knowledge_from_config
from magi.core.memory import (
    build_memory_from_config,
    operator_settings_store,
    resolve_memory_settings,
)
from magi.core.sandbox import build_sandbox_from_config
from magi.core.session_index import open_session_index


def build_conversation_service(
    *,
    channel_guidance: str,
    db: BaseDb | None = None,
    member_builders: Sequence[Callable[[Model], Agent]] | None = None,
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

    # The self-evolution queue (None when off), built ONCE over the resolved
    # memory root (operator override + `~` expansion) and shared by the curator,
    # the team's propose tools, and — through the same resolver — the admin
    # queue, so every proposal lands where the operator reviews it.
    evolution_store = None
    if config.evolution_enabled:
        from magi.agent.skills import evolution_proposable_targets
        from magi.core.evolution import EvolutionStore

        memory_root = resolve_memory_settings(operator_settings_store().read_memory()).memory_dir
        evolution_store = EvolutionStore(
            Path(memory_root), proposable=evolution_proposable_targets()
        )

    # The curator owns durable memory when on (it supersedes the long-term
    # summarizer and the lead's write tools). Needs a model, so the agent layer
    # builds it; magi/core/memory receives it as an injected callable.
    curate_fn = None
    if config.memory_curation:
        from magi.agent.curator import build_memory_curator

        curate_fn = build_memory_curator(evolution_store)
        log_info("memory: curation ENABLED (post-turn durable-memory pass)")
    else:
        log_info("memory: curation DISABLED")

    # The pre-reply mood pass needs a model, so the agent layer builds it;
    # magi/core/conversation stays model-free and receives it as an injected
    # callable (see MoodFn). Gated by config.
    mood_fn = None
    if config.mood_enabled:
        from magi.agent.mood import build_mood_pass

        mood_fn = build_mood_pass()
        log_info(f"mood: pre-reply pass ENABLED (vocabulary: {sorted(config.mood_vocabulary)})")
    else:
        log_info("mood: pre-reply pass DISABLED")

    memory = build_memory_from_config(
        summarize_session_fn=session_fn,
        curate_fn=curate_fn,
    )
    # The knowledge RAG store (None when the feature is off) is built once here and
    # injected into both consumers: the team (its search tool) and the conversation
    # service (context auto-injection). One instance, one connection lifecycle.
    knowledge = build_knowledge_from_config()
    # Full-text index of finished turns: written by the service, searched by
    # the lead's search_sessions tool. None when off or FTS5 is missing.
    session_index = (
        open_session_index(Path(config.session_index_path))
        if config.session_search_enabled
        else None
    )
    # The run_command sandbox (None unless sandbox.backend is set): the team gets
    # the tool, the service resolves /approve and /deny on every channel.
    sandbox = build_sandbox_from_config()
    team = build_team(
        memory,
        db,
        member_builders,
        knowledge=knowledge,
        session_index=session_index,
        sandbox=sandbox,
        evolution_store=evolution_store,
    )
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
        mood_fn=mood_fn,
        session_index=session_index,
        sandbox=sandbox,
    )
