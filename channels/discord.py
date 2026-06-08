"""Discord channel — wraps the project's DiscordClient around the agent team.

`DiscordClient` (clients.mydiscord) forwards a per-user `user_id` and per-chat
`session_id`, so long-term memory and short-term history are scoped correctly out
of the box. `db` is injectable for tests / alternate stores.
"""

from agno.db.base import BaseDb
from agno.utils.log import log_info

from agent.team import build_team
from clients.mydiscord import DiscordClient
from core.config import config
from core.memory import get_memory
from core.prompts import load_prompt


def build_discord_client(db: BaseDb | None = None) -> DiscordClient:
    """Build the Discord bot backed by the multimodal agent team."""
    config.log_settings()
    if not config.DISCORD_BOT_TOKEN:
        raise RuntimeError("DISCORD_BOT_TOKEN not set in environment")
    log_info(f"building discord client (db={'injected' if db else 'default'})")

    memory = get_memory()
    # Auto-summarize is the one piece that needs a model, so it's attached here
    # (the agent layer) rather than in core. Off unless SUMMARIZE_ON_EVICT is set.
    if config.summarize_on_evict and memory.summarize_fn is None:
        from agent.summarizer import build_summarizer

        memory.summarize_fn = build_summarizer()
        log_info(f"memory: auto-summarize on evict ENABLED (every {memory.summarize_every} turns)")

    return DiscordClient(
        team=build_team(db),
        memory=memory,
        # Discord-only output rules, injected per run so the lead prompt stays
        # channel-agnostic (see prompts/channels/discord.md).
        channel_guidance=load_prompt("channels/discord.md"),
    )
