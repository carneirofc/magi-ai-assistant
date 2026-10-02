"""Tests for the Telegram channel (magi/channels/telegram) — the transport-free
`respond` path, driven with fake senders (no python-telegram-bot, no network)."""

import pytest
from agno.media import Image

from magi.channels.telegram import (
    MESSAGE_LIMIT,
    Inbound,
    Outbound,
    TelegramAdapter,
    build_telegram_adapter,
    chunk,
    session_id_for,
)
from magi.core.config import configure
from magi.core.conversation import ConversationReply


class _FakeService:
    def __init__(self, reply: ConversationReply) -> None:
        self.reply = reply
        self.calls: list[dict[str, object]] = []
        self.flushed: list[tuple[str, str]] = []

    async def handle(self, **kwargs: object) -> ConversationReply:
        self.calls.append(kwargs)
        return self.reply

    def flush(self, user_id: str, session_id: str) -> int:
        self.flushed.append((user_id, session_id))
        return 3

    def with_guidance(self, guidance: str) -> _FakeService:
        self.guidance = guidance
        return self


class _Sink:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.photos: list[bytes | str] = []
        self.docs: list[bytes | str] = []

    def outbound(self) -> Outbound:
        async def text(t: str) -> None:
            self.texts.append(t)

        async def photo(p: bytes | str) -> None:
            self.photos.append(p)

        async def doc(d: bytes | str) -> None:
            self.docs.append(d)

        return Outbound(text=text, photo=photo, document=doc)


def _adapter(reply: str = "hello", allowed: tuple[int, ...] = (7,)):
    service = _FakeService(ConversationReply(text=reply))
    return TelegramAdapter(service, token="t", allowed_users=allowed), service  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]


async def test_unknown_user_is_refused_with_their_id():
    adapter, service = _adapter()
    sink = _Sink()
    await adapter.respond(Inbound(user_id=99, chat_id=1, text="hi"), sink.outbound())
    assert service.calls == []
    assert "99" in sink.texts[0]


async def test_empty_allowlist_refuses_everyone():
    adapter, service = _adapter(allowed=())
    sink = _Sink()
    await adapter.respond(Inbound(user_id=7, chat_id=1, text="hi"), sink.outbound())
    assert service.calls == []


async def test_whoami_works_before_allowlisting():
    adapter, _ = _adapter(allowed=())
    sink = _Sink()
    await adapter.respond(Inbound(user_id=42, chat_id=1, text="/whoami"), sink.outbound())
    assert sink.texts == ["Your Telegram user id is 42."]


async def test_allowed_user_turn_is_scoped_and_replied():
    adapter, service = _adapter(reply="pong")
    sink = _Sink()
    await adapter.respond(Inbound(user_id=7, chat_id=5, text="ping"), sink.outbound())
    (call,) = service.calls
    assert call["user_id"] == "telegram:7"
    assert call["session_id"] == session_id_for(5)
    assert call["text"] == "ping"
    assert sink.texts == ["pong"]


async def test_new_command_flushes_this_chat():
    adapter, service = _adapter()
    sink = _Sink()
    await adapter.respond(Inbound(user_id=7, chat_id=5, text="/new@magi_bot"), sink.outbound())
    assert service.flushed == [("telegram:7", "tg-5")]
    assert "3" in sink.texts[0]


async def test_photo_rides_as_media_and_reply_images_are_sent():
    adapter, service = _adapter()
    service.reply = ConversationReply(text="", images=(Image(content=b"png"),))
    sink = _Sink()
    await adapter.respond(
        Inbound(user_id=7, chat_id=5, text="", images=[Image(content=b"in")]), sink.outbound()
    )
    (call,) = service.calls
    media = call["media"]
    assert isinstance(media, dict) and len(media["images"]) == 1
    assert sink.photos == [b"png"] and sink.texts == []


async def test_long_reply_is_chunked():
    adapter, _ = _adapter(reply="x" * (MESSAGE_LIMIT + 10))
    sink = _Sink()
    await adapter.respond(Inbound(user_id=7, chat_id=5, text="go"), sink.outbound())
    assert [len(t) for t in sink.texts] == [MESSAGE_LIMIT, 10]


async def test_empty_reply_sends_fallback():
    adapter, _ = _adapter(reply="   ")
    sink = _Sink()
    await adapter.respond(Inbound(user_id=7, chat_id=5, text="go"), sink.outbound())
    assert len(sink.texts) == 1 and "nothing to send" in sink.texts[0]


def test_chunk_roundtrips():
    text = "abc" * 5000
    assert "".join(chunk(text)) == text


def test_build_requires_token():
    configure(telegram_bot_token=None)
    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
        build_telegram_adapter(_FakeService(ConversationReply(text="")))  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]


def test_build_applies_telegram_guidance():
    configure(telegram_bot_token="tok", telegram_allowed_users=[1, 2])
    service = _FakeService(ConversationReply(text=""))
    adapter = build_telegram_adapter(service)  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]
    assert adapter.allowed_users == frozenset({1, 2})
    assert "Telegram" in service.guidance
