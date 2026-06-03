"""Discord channel — wraps the project's custom DiscordClient (clients.mydiscord)
around the project agent (or team).

DiscordClient forwards per-user `user_id` (Discord author id) and a per-chat
`session_id` (channel id by default, or thread id once a thread is created on
request), so long-term memory and short-term history are scoped correctly out of
the box. `db` is injectable for tests / alternate stores.
"""

from agno.db.base import BaseDb
from agno.utils.log import log_info

from agent import build_discord_agent, build_team
from clients.mydiscord import DiscordClient
from core.config import config


def build_discord_client(
    use_team: bool = False, db: BaseDb | None = None
) -> DiscordClient:
    if not config.DISCORD_BOT_TOKEN:
        raise RuntimeError("DISCORD_BOT_TOKEN not set in environment")
    log_info(
        f"building discord client: mode={'team' if use_team else 'agent'}, "
        f"db={'injected' if db else 'default'}"
    )
    if use_team:
        team = build_team(db=db)
        return DiscordClient(team=team)
    return DiscordClient(agent=build_discord_agent(db=db))
