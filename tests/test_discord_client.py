from types import SimpleNamespace

import pytest

from clients.mydiscord import DiscordClient


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
async def test_handle_response_in_thread_sends_fallback_for_empty_content():
    client = DiscordClient.__new__(DiscordClient)
    thread = DummyTarget()
    response = SimpleNamespace(reasoning_content=None, content="   ")

    await client._handle_response_in_thread(response, thread)

    assert thread.sent == ["I finished processing that, but there was no text content to send."]
