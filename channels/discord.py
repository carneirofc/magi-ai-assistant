"""Discord channel — wraps agno DiscordClient around the project agent (or team).

DiscordClient already forwards per-user `user_id` (Discord author id) and
per-thread `session_id`, so long-term memory and short-term history are scoped
correctly out of the box.
"""

from agno.integrations.discord import DiscordClient

from agent.factory import build_discord_agent
from agent.team import build_team
from core.config import config


def build_discord_client(use_team: bool = False) -> DiscordClient:
    if not config.DISCORD_BOT_TOKEN:
        raise RuntimeError("DISCORD_BOT_TOKEN not set in environment")
    if use_team:
        return DiscordClient(team=build_team())
    return DiscordClient(agent=build_discord_agent())
