"""The top-level factory — one call to build a self-contained assistant.

`Assistant.create(config)` is the library's front door: hand it an immutable
`Config` and it constructs the whole stack — the `AgentContext` (config + shared
services), the deliberate `MemoryManager`, the specialist `Team`, and the
`ConversationService` that drives one turn end to end — then hands back a small
object you can either chat with directly or wire into a channel.

    from magi import Assistant, Config

    assistant = Assistant.create(Config(model_provider="llamacpp", lead_model_id="qwen3.5-9b"))
    reply = await assistant.chat(user_id="u1", session_id="s1", text="hello")

Because everything hangs off the explicit `Config` carried in `ctx` — no process
global — two assistants with different configs can live in the same process. A
channel (Discord, HTTP API) builds its own `ConversationService` from the same
`ctx` via `magi.channels.bootstrap.build_conversation_service`, adding only its
transport-specific pieces; `assistant.conversation` is the channel-neutral one.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Optional

from agno.agent import Agent
from agno.db.base import BaseDb
from agno.models.base import Model

from magi.channels.bootstrap import build_conversation_service
from magi.core.config import Config
from magi.core.context import AgentContext
from magi.core.conversation import ConversationReply, ConversationService

MemberBuilder = Callable[[AgentContext, Model], Agent]


@dataclass
class Assistant:
    """A fully assembled assistant: its runtime context and conversation service.

    Build it with `Assistant.create(...)`; drive it with `chat(...)` /
    `chat_stream(...)`, or hand `assistant.conversation` to a transport.
    """

    ctx: AgentContext
    conversation: ConversationService

    @classmethod
    def create(
        cls,
        config: Config,
        *,
        channel_guidance: str = "",
        db: Optional[BaseDb] = None,
        member_builders: Optional[Sequence[MemberBuilder]] = None,
    ) -> "Assistant":
        """Assemble the whole stack from an explicit `Config`.

        `channel_guidance` is optional channel-specific output rules appended to
        each run's context (empty for a bare/library assistant). `db` overrides the
        shared persistence backend (defaults to the one `ctx` builds from
        `config.db_file`). `member_builders` overrides the specialist roster
        (defaults to the registry).
        """
        ctx = AgentContext(config=config)
        conversation = build_conversation_service(
            ctx,
            channel_guidance=channel_guidance,
            db=db,
            member_builders=member_builders,
        )
        return cls(ctx=ctx, conversation=conversation)

    async def chat(
        self,
        *,
        user_id: str,
        session_id: str,
        text: str,
        media: Optional[dict] = None,
    ) -> ConversationReply:
        """Run one turn end to end and return the channel-neutral reply."""
        return await self.conversation.handle(
            user_id=user_id, session_id=session_id, text=text, media=media
        )

    def chat_stream(
        self,
        *,
        user_id: str,
        session_id: str,
        text: str,
        media: Optional[dict] = None,
    ):
        """Run one turn, yielding reply deltas then the final reply (see
        `ConversationService.handle_stream`)."""
        return self.conversation.handle_stream(
            user_id=user_id, session_id=session_id, text=text, media=media
        )

    def flush(self, user_id: str, session_id: str) -> int:
        """Close a session (fold its summary into an episode, wipe live turns)."""
        return self.conversation.flush(user_id, session_id)
