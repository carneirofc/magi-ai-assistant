import pytest

from clients.mydiscord import DiscordClient
from core.conversation import ConversationReply


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

    def typing(self):
        return self.typing_cm

    async def send(self, message):
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
