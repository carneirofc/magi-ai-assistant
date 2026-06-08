"""Discord channel composition root.

Builds every dependency once and injects it — no globals, nothing constructed
inside a constructor. Wiring order:

    summarizers (gated by config) -> memory -> team(memory) ->
    ConversationService(team, memory, channel_guidance) ->
    discord.Client -> DiscordClient(conversation, client)
"""

from agno.db.base import BaseDb
from agno.utils.log import log_info

from agent.team import build_team
from clients.mydiscord import DiscordClient
from core.config import config
from core.conversation import ConversationService
from core.memory import build_memory_from_config
from core.prompts import load_prompt

try:
    import discord
except (ImportError, ModuleNotFoundError):
    raise ImportError("`discord.py` not installed. Please install using `pip install discord.py`")


def build_discord_client(db: BaseDb | None = None) -> DiscordClient:
    """Build the Discord bot backed by the multimodal agent team."""
    config.log_settings()
    if not config.DISCORD_BOT_TOKEN:
        raise RuntimeError("DISCORD_BOT_TOKEN not set in environment")
    log_info(f"building discord client (db={'injected' if db else 'default'})")

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

    memory = build_memory_from_config(
        summarize_session_fn=session_fn,
        summarize_long_term_fn=long_term_fn,
    )
    team = build_team(memory, db)
    conversation = ConversationService(
        runner=team,
        memory=memory,
        # Discord-only output rules, kept out of the base prompt so it stays
        # channel-agnostic (see prompts/channels/discord.md).
        channel_guidance=load_prompt("channels/discord.md"),
    )

    intents = discord.Intents.all()
    client = discord.Client(intents=intents)
    return DiscordClient(conversation=conversation, client=client)
