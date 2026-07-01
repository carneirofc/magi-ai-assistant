"""Per-run Discord context for tools that act on the current conversation.

The Discord client sets this once per inbound message before calling the team.
Tools can then operate on the live `discord.py` channel/message objects for THIS
conversation only, without asking the model to guess channel or guild ids.

The live channel/message are typed as Protocols (the narrow slice this code
actually touches), so nothing here depends on `Any` or on importing discord.py.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Protocol

# Pause between successive delete requests so a large batch does not burst into
# Discord's rate limiter and trigger 429 retries.
_DELETE_THROTTLE_SECONDS = 1.0


class DiscordMessage(Protocol):
    """The slice of a discord.py message this code identifies and deletes."""

    id: int

    async def delete(self) -> None: ...


class DiscordChannel(Protocol):
    """The slice of a discord.py channel the tools and context touch.

    A live channel may be a guild TextChannel, a Thread, or a DMChannel; this is
    the common surface they all share.
    """

    def history(self, *, limit: int) -> AsyncIterator[DiscordMessage]: ...

    def get_partial_message(self, message_id: int) -> DiscordMessage: ...


@dataclass(slots=True)
class DiscordRunContext:
    guild_id: str | None
    guild_name: str | None
    channel_id: str
    channel_name: str | None
    channel_kind: str
    message_id: str
    message_url: str
    message_text: str
    # The RAW Discord snowflake — for Discord-native tool use (e.g. moderation
    # tools that address Discord's own API). NOT the same string as the
    # memory-scoping user_id ConversationService sees, which is namespaced via
    # magi.channels.gateway.scoped_user_id (see ADR 0003).
    user_id: str
    username: str
    channel: DiscordChannel

    def as_dict(self) -> dict[str, str | None]:
        # Hand-built rather than dataclasses.asdict(): asdict() deep-copies every
        # field, and the live `channel` holds unpicklable asyncio Futures. The
        # channel is also dropped here — it is not serialisable for the model.
        return {
            "guild_id": self.guild_id,
            "guild_name": self.guild_name,
            "channel_id": self.channel_id,
            "channel_name": self.channel_name,
            "channel_kind": self.channel_kind,
            "message_id": self.message_id,
            "message_url": self.message_url,
            "user_id": self.user_id,
            "username": self.username,
        }

    async def delete_messages(self, messages: Sequence[DiscordMessage]) -> None:
        """Delete the messages one at a time, pausing between requests.

        discord.py exposes a bulk endpoint, but it rejects messages older than 14
        days and works only on guild channels; deleting individually is the path
        that works everywhere, and `_throttled` keeps the cadence under Discord's
        per-route rate limit.
        """
        await self._throttled(messages, lambda message: message.delete())

    @staticmethod
    async def _throttled[T](items: Sequence[T], action: Callable[[T], Awaitable[None]]) -> None:
        """Run `action` over items, pausing between calls to dodge 429s.

        The pause only goes between requests, not before the first or after the
        last, so a single item incurs no delay.
        """
        for index, item in enumerate(items):
            if index:
                await asyncio.sleep(_DELETE_THROTTLE_SECONDS)
            await action(item)


_CURRENT_DISCORD_CONTEXT: ContextVar[DiscordRunContext | None] = ContextVar(
    "current_discord_context",
    default=None,
)


def set_current_discord_context(context: DiscordRunContext) -> Token[DiscordRunContext | None]:
    return _CURRENT_DISCORD_CONTEXT.set(context)


def reset_current_discord_context(token: Token[DiscordRunContext | None]) -> None:
    _CURRENT_DISCORD_CONTEXT.reset(token)


def get_current_discord_context() -> DiscordRunContext:
    context = _CURRENT_DISCORD_CONTEXT.get()
    if context is None:
        raise RuntimeError("No active Discord context for this tool call")
    return context
