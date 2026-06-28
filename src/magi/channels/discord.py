"""Discord channel composition root.

The shared brain (summarizers -> memory -> team -> ConversationService) comes
from `channels.bootstrap`; this module adds only what is Discord's: the output
guidance prompt, the gateway client, and the `DiscordClient` presentation layer.
Everything is injected — no globals, nothing constructed inside a constructor.
"""

from agno.db.base import BaseDb
from agno.utils.log import log_info

from magi.agent.model import lead_model_def
from magi.channels.bootstrap import build_conversation_service
from clients.mydiscord import DiscordClient
from magi.core.config import config
from magi.core.prompts import load_prompt

try:
    import discord
except (ImportError, ModuleNotFoundError):
    raise ImportError("`discord.py` not installed. Please install using `pip install discord.py`")


def build_discord_client(db: BaseDb | None = None) -> DiscordClient:
    """Build the Discord bot backed by the multimodal agent team."""
    if not config.DISCORD_BOT_TOKEN:
        raise RuntimeError("DISCORD_BOT_TOKEN not set in environment")
    log_info(f"building discord client (db={'injected' if db else 'default'})")

    conversation = build_conversation_service(
        # Discord-only output rules, kept out of the base prompt so it stays
        # channel-agnostic (see prompts/channels/discord.md).
        channel_guidance=load_prompt("channels/discord.md"),
        db=db,
    )

    log_info("discord client: building with all intents")
    client = discord.Client(intents=discord.Intents.all())
    return DiscordClient(
        conversation=conversation,
        client=client,
        token=config.DISCORD_BOT_TOKEN,
        # Inbound audio is only wired into runs when the lead can actually hear
        # it (vision-only backends reject `input_audio` parts).
        supports_audio=lead_model_def().supports_audio,
    )
