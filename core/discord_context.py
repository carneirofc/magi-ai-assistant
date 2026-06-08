"""Per-run Discord context for tools that act on the current conversation.

The Discord client sets this once per inbound message before calling the team.
Tools can then operate on the live `discord.py` channel/message objects for THIS
conversation only, without asking the model to guess channel or guild ids.
"""

from contextvars import ContextVar, Token
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class DiscordRunContext:
    guild_id: str | None
    guild_name: str | None
    channel_id: str
    channel_name: str | None
    channel_kind: str
    message_id: str
    message_url: str
    user_id: str
    username: str
    channel: Any

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("channel", None)
        return data


_CURRENT_DISCORD_CONTEXT: ContextVar[DiscordRunContext | None] = ContextVar(
    "current_discord_context",
    default=None,
)


def set_current_discord_context(context: DiscordRunContext) -> Token:
    return _CURRENT_DISCORD_CONTEXT.set(context)


def reset_current_discord_context(token: Token) -> None:
    _CURRENT_DISCORD_CONTEXT.reset(token)


def get_current_discord_context() -> DiscordRunContext:
    context = _CURRENT_DISCORD_CONTEXT.get()
    if context is None:
        raise RuntimeError("No active Discord context for this tool call")
    return context
