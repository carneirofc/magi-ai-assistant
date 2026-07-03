"""In-process backend: drive the assembled brain directly, no HTTP server.

`EmbeddedClient` wraps a `ConversationService` (the same object every channel
drives) and exposes it through the channel-neutral `MagiClient` surface. A Python
GUI (PyQt/PySide, Flet, Toga, Tkinter, …) embeds the whole assistant in its own
process — no port to bind, no server to run alongside.

Memory scope matches the HTTP path exactly: both namespace `user_id` under the
same platform (`scoped_user_id`), so the same `user_id` reaches the same durable
memory whether the app runs embedded or talks to a server.

Build one with `magi.client.embed(...)` (the composition root that wires the
stack from config); this class stays a thin, fully-injected adapter so it's
trivial to test against a fake `ConversationService`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Optional, Union

from agno.media import Image

from magi.channels.gateway import scoped_user_id
from magi.client.types import Delta, InboundImage, Media, Reply
from magi.core.conversation import ConversationDelta, ConversationReply, ConversationService


def _subtype(mime: Optional[str]) -> Optional[str]:
    """agno's `format` — the subtype of a mime type ("image/png" -> "png")."""
    return mime.split("/", 1)[1] if mime and "/" in mime else None


def _to_agno_media(images: Sequence[InboundImage]) -> dict[str, list[Image]]:
    """The `ConversationService` media kwargs for a turn's inbound images.

    Inline bytes are handed over directly (a local backend can't fetch URLs);
    an http(s) URL passes by reference. Items with neither are dropped.
    """
    built: list[Image] = []
    for img in images:
        if img.data is not None:
            built.append(Image(content=img.data, mime_type=img.mime_type, format=_subtype(img.mime_type)))
        elif img.url:
            built.append(Image(url=img.url, mime_type=img.mime_type))
    return {"images": built} if built else {}


def reply_from_conversation(reply: ConversationReply) -> Reply:
    """Map an agno-backed `ConversationReply` onto the plain `Reply`.

    Mirrors the HTTP channel's media serialization (`channels.api._media_items`),
    but keeps inline media as raw bytes rather than base64 — in-process there's
    no wire to cross.
    """
    media: list[Media] = []
    for kind, items in (
        ("image", reply.images),
        ("video", reply.videos),
        ("audio", reply.audio),
        ("file", reply.files),
    ):
        for m in items:
            content = getattr(m, "content", None)
            url = getattr(m, "url", None)
            if not isinstance(content, bytes) and not url:
                continue  # nothing to render — same drop the wire does
            media.append(
                Media(
                    kind=kind,
                    mime_type=getattr(m, "mime_type", None),
                    filename=getattr(m, "filename", None),
                    url=None if isinstance(content, bytes) else url,
                    data=content if isinstance(content, bytes) else None,
                )
            )
    return Reply(
        text=reply.text,
        reasoning=reply.reasoning,
        is_error=reply.is_error,
        media=tuple(media),
    )


class EmbeddedClient:
    """A `MagiClient` over an in-process `ConversationService`.

    `mcp_toolkits` (if any member talks over MCP) are connected in `aopen` and
    closed in `aclose`, the same lifecycle the HTTP app runs over its FastAPI
    lifespan — pre-connecting keeps the session warm and surfaces the member's
    tools before the first turn. Best-effort: a connect failure is swallowed
    (agno retries per run), so a desktop app still boots if the MCP server is down.
    """

    def __init__(
        self,
        conversation: ConversationService,
        user_id: str,
        session_id: str = "default",
        *,
        platform: str = "api",
        mcp_toolkits: Sequence[object] = (),
    ) -> None:
        self._conversation = conversation
        self.user_id = user_id
        self.session_id = session_id
        # Same scoping the HTTP channel applies server-side, so embedded and HTTP
        # with the same user_id share one memory scope (default platform "api").
        self._scoped = scoped_user_id(platform, user_id)
        self._mcp_toolkits = list(mcp_toolkits)
        self._opened = False

    async def aopen(self) -> "EmbeddedClient":
        if not self._opened:
            for tk in self._mcp_toolkits:
                connect = getattr(tk, "connect", None)
                if connect is not None:
                    try:
                        await connect()
                    except Exception:  # noqa: BLE001 - degrade, don't fail boot
                        pass
            self._opened = True
        return self

    async def aclose(self) -> None:
        for tk in self._mcp_toolkits:
            close = getattr(tk, "close", None)
            if close is not None:
                try:
                    await close()
                except Exception:  # noqa: BLE001 - shutdown is best-effort
                    pass
        self._opened = False

    async def __aenter__(self) -> "EmbeddedClient":
        return await self.aopen()

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def send(self, text: str, *, images: Sequence[InboundImage] = ()) -> Reply:
        reply = await self._conversation.handle(
            user_id=self._scoped,
            session_id=self.session_id,
            text=text,
            media=_to_agno_media(images),
        )
        return reply_from_conversation(reply)

    async def stream(
        self, text: str, *, images: Sequence[InboundImage] = ()
    ) -> AsyncIterator[Union[Delta, Reply]]:
        async for item in self._conversation.handle_stream(
            user_id=self._scoped,
            session_id=self.session_id,
            text=text,
            media=_to_agno_media(images),
        ):
            if isinstance(item, ConversationDelta):
                yield Delta(text=item.text)
            else:
                yield reply_from_conversation(item)

    async def flush(self) -> int:
        return self._conversation.flush(self._scoped, self.session_id)

    async def context_stats(self) -> dict[str, object]:
        return self._conversation.context_stats(self._scoped, self.session_id)
