import pytest
from agno.media import Audio, Image

from clients.mydiscord import DiscordClient
from magi.core.conversation import ConversationReply


class DummyCommandChannel:
    def __init__(self):
        self.id = 555
        self.sent = []

    async def send(self, message):
        self.sent.append(message)


class _FakeConversation:
    """Records the (verb, user_id, session_id) each control command called
    with, so tests can assert the id was namespaced (see gateway.scoped_user_id,
    ADR 0003) before it reached `ConversationService`."""

    def __init__(self):
        self.calls: list[tuple] = []

    def flush(self, user_id, session_id):
        self.calls.append(("flush", user_id, session_id))
        return 3

    def context_stats(self, user_id, session_id):
        self.calls.append(("context_stats", user_id, session_id))
        return {
            "est_tokens": 10,
            "ratio": 0.1,
            "budget_tokens": 100,
            "short_term_turns": 1,
            "sections": {"short_term": 1, "long_term": 2, "episodes": 3, "persona": 4},
        }


@pytest.mark.asyncio
async def test_flush_command_scopes_user_id_by_platform():
    client = DiscordClient.__new__(DiscordClient)
    client.conversation = _FakeConversation()
    channel = DummyCommandChannel()

    handled = await client._maybe_handle_command(channel, "!flush", 42)

    assert handled is True
    assert client.conversation.calls == [("flush", "discord:42", "555")]


@pytest.mark.asyncio
async def test_context_command_scopes_user_id_by_platform():
    client = DiscordClient.__new__(DiscordClient)
    client.conversation = _FakeConversation()
    channel = DummyCommandChannel()

    handled = await client._maybe_handle_command(channel, "!ctx", 42)

    assert handled is True
    assert client.conversation.calls == [("context_stats", "discord:42", "555")]


class DummyTyping:
    def __init__(self):
        self.exit_args = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.exit_args = (exc_type, exc, tb)
        return False


class DummyTarget:
    def __init__(self):
        self.id = 123
        self.typing_cm = DummyTyping()
        self.sent = []
        self.sent_files = []  # list of file batches (discord.File lists)

    def typing(self):
        return self.typing_cm

    async def send(self, message=None, *, files=None):
        if files is not None:
            self.sent_files.append(files)
        if message is not None:
            self.sent.append(message)


@pytest.mark.asyncio
async def test_safe_typing_preserves_inner_exception():
    client = DiscordClient.__new__(DiscordClient)
    target = DummyTarget()

    with pytest.raises(ValueError, match="boom"):
        async with client._safe_typing(target):
            raise ValueError("boom")

    assert target.typing_cm.exit_args[0] is ValueError


@pytest.mark.asyncio
async def test_short_message_sends_once_without_batch_numbering():
    client = DiscordClient.__new__(DiscordClient)
    target = DummyTarget()

    sent = await client._send_discord_messages(thread=target, message="hi there")

    assert sent is True
    assert target.sent == ["hi there"]


@pytest.mark.asyncio
async def test_long_message_is_split_and_each_part_numbered():
    from clients.chunking import DISCORD_MESSAGE_LIMIT

    client = DiscordClient.__new__(DiscordClient)
    target = DummyTarget()
    message = "a" * DISCORD_MESSAGE_LIMIT + "b" * 10

    sent = await client._send_discord_messages(thread=target, message=message)

    assert sent is True
    assert len(target.sent) == 2
    assert target.sent[0].startswith("[1/2] ")
    assert target.sent[1].startswith("[2/2] ")
    # The numbering is the only thing added; bodies reconstruct the original.
    bodies = [m.split("] ", 1)[1] for m in target.sent]
    assert "".join(bodies) == message


@pytest.mark.asyncio
async def test_empty_message_is_skipped():
    client = DiscordClient.__new__(DiscordClient)
    target = DummyTarget()

    sent = await client._send_discord_messages(thread=target, message="   ")

    assert sent is False
    assert target.sent == []


@pytest.mark.asyncio
async def test_italics_wraps_each_line():
    client = DiscordClient.__new__(DiscordClient)
    target = DummyTarget()

    sent = await client._send_discord_messages(
        thread=target, message="one\ntwo", italics=True
    )

    assert sent is True
    assert target.sent == ["_one_\n_two_"]


@pytest.mark.asyncio
async def test_send_reply_sends_fallback_for_empty_content():
    client = DiscordClient.__new__(DiscordClient)
    thread = DummyTarget()
    reply = ConversationReply(text="   ", reasoning=None)

    await client._send_reply(reply, thread)

    assert thread.sent == ["I finished processing that, but there was no text content to send."]


# --- outbound media -----------------------------------------------------------
@pytest.mark.asyncio
async def test_reply_media_is_uploaded_as_attachments():
    client = DiscordClient.__new__(DiscordClient)
    target = DummyTarget()
    reply = ConversationReply(
        text="here you go",
        images=(Image(content=b"png-bytes", mime_type="image/png", format="png"),),
        audio=(Audio(content=b"wav-bytes", format="wav"),),
    )

    await client._send_reply(reply, target)

    assert target.sent == ["here you go"]
    (batch,) = target.sent_files
    assert [f.filename for f in batch] == ["image-1.png", "audio-1.wav"]
    assert batch[0].fp.read() == b"png-bytes"


@pytest.mark.asyncio
async def test_media_only_reply_skips_no_text_fallback():
    client = DiscordClient.__new__(DiscordClient)
    target = DummyTarget()
    reply = ConversationReply(text="", images=(Image(content=b"x", format="png"),))

    await client._send_reply(reply, target)

    assert target.sent == []  # no fallback notice — media WAS the reply
    assert len(target.sent_files) == 1


@pytest.mark.asyncio
async def test_url_media_is_fetched_and_uploaded(monkeypatch):
    client = DiscordClient.__new__(DiscordClient)
    target = DummyTarget()

    async def fake_fetch(url):
        assert url == "https://cdn.example/cover.jpg"
        return b"jpg-bytes"

    monkeypatch.setattr(client, "_fetch_bytes", fake_fetch)
    reply = ConversationReply(text="t", images=(Image(url="https://cdn.example/cover.jpg"),))

    await client._send_reply(reply, target)

    (batch,) = target.sent_files
    assert batch[0].filename == "cover.jpg"
    assert batch[0].fp.read() == b"jpg-bytes"


@pytest.mark.asyncio
async def test_oversized_media_falls_back_to_link():
    import clients.mydiscord as mydiscord

    client = DiscordClient.__new__(DiscordClient)
    target = DummyTarget()
    big = b"x" * (mydiscord._MAX_UPLOAD_BYTES + 1)
    reply = ConversationReply(text="", images=(Image(content=big),))

    await client._send_reply(reply, target)

    assert target.sent_files == []
    assert any("too large" in m for m in target.sent)


@pytest.mark.asyncio
async def test_unfetchable_url_media_degrades_to_link(monkeypatch):
    client = DiscordClient.__new__(DiscordClient)
    target = DummyTarget()

    async def fake_fetch(url):
        return None

    monkeypatch.setattr(client, "_fetch_bytes", fake_fetch)
    reply = ConversationReply(text="", images=(Image(url="https://cdn.example/x.png"),))

    await client._send_reply(reply, target)

    assert target.sent_files == []
    assert any("https://cdn.example/x.png" in m for m in target.sent)


# --- inbound media ------------------------------------------------------------
class FakeAttachment:
    def __init__(self, *, filename, content_type, data=b"data", size=None):
        self.filename = filename
        self.content_type = content_type
        self.size = size if size is not None else len(data)
        self._data = data

    async def read(self):
        return self._data


class FakeMessage:
    def __init__(self, *, content="", attachments=(), stickers=()):
        self.content = content
        self.attachments = list(attachments)
        self.stickers = list(stickers)


@pytest.mark.asyncio
async def test_extract_media_processes_all_attachments():
    client = DiscordClient.__new__(DiscordClient)
    client.supports_audio = True
    message = FakeMessage(
        attachments=[
            FakeAttachment(filename="a.png", content_type="image/png", data=b"img1"),
            FakeAttachment(filename="b.jpg", content_type="image/jpeg", data=b"img2"),
            FakeAttachment(filename="v.ogg", content_type="audio/ogg", data=b"voice"),
            FakeAttachment(filename="doc.pdf", content_type="application/pdf", data=b"pdf"),
        ]
    )

    media, notes = await client._extract_media(message)

    assert [i.content for i in media["images"]] == [b"img1", b"img2"]
    assert media["audio"][0].content == b"voice" and media["audio"][0].format == "ogg"
    assert media["files"][0].filename == "doc.pdf"
    assert media["videos"] is None
    assert len(notes) == 4


@pytest.mark.asyncio
async def test_extract_media_gates_audio_when_model_cannot_hear():
    client = DiscordClient.__new__(DiscordClient)
    client.supports_audio = False
    message = FakeMessage(
        attachments=[FakeAttachment(filename="v.ogg", content_type="audio/ogg")]
    )

    media, notes = await client._extract_media(message)

    assert media["audio"] is None
    assert any("cannot listen to audio" in n for n in notes)


@pytest.mark.asyncio
async def test_extract_media_wires_custom_emoji_as_images(monkeypatch):
    client = DiscordClient.__new__(DiscordClient)
    client.supports_audio = True

    fetched = []

    async def fake_fetch(url):
        fetched.append(url)
        return b"emoji-bytes"

    monkeypatch.setattr(DiscordClient, "_fetch_bytes", staticmethod(fake_fetch))
    message = FakeMessage(content="nice <:pog:123456789012345678> and <a:wave:876543210987654321>")

    media, notes = await client._extract_media(message)

    assert fetched == [
        "https://cdn.discordapp.com/emojis/123456789012345678.png",
        "https://cdn.discordapp.com/emojis/876543210987654321.gif",
    ]
    assert [i.content for i in media["images"]] == [b"emoji-bytes", b"emoji-bytes"]
    assert any(":pog:" in n for n in notes) and any(":wave:" in n for n in notes)


@pytest.mark.asyncio
async def test_extract_media_skips_oversized_attachment():
    import clients.mydiscord as mydiscord

    client = DiscordClient.__new__(DiscordClient)
    client.supports_audio = True
    message = FakeMessage(
        attachments=[
            FakeAttachment(
                filename="huge.png",
                content_type="image/png",
                size=mydiscord._MAX_INBOUND_BYTES + 1,
            )
        ]
    )

    media, notes = await client._extract_media(message)

    assert media["images"] is None
    assert any("too large" in n for n in notes)
