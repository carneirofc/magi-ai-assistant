import threading
import json
from datetime import UTC, datetime

import pytest

from agent.tools.discord import (
    delete_discord_message,
    delete_discord_messages,
    delete_recent_discord_messages,
    describe_current_discord_context,
    list_recent_discord_messages,
)
from core.discord_context import (
    DiscordRunContext,
    reset_current_discord_context,
    set_current_discord_context,
)


def _tool_text(result: dict) -> str:
    return f"{result.get('message', '')} {json.dumps(result.get('data'), ensure_ascii=False)}"


class DummyAuthor:
    def __init__(self, name: str, author_id: int, *, bot: bool = False):
        self.name = name
        self.id = author_id
        self.bot = bot


class DummyMessage:
    def __init__(self, message_id: int, author: DummyAuthor, content: str, *, pinned: bool = False):
        self.id = message_id
        self.author = author
        self.content = content
        self.pinned = pinned
        self.attachments = []
        self.created_at = datetime(2026, 6, 8, tzinfo=UTC)
        self.deleted = False

    async def delete(self):
        self.deleted = True


class DummyHistory:
    def __init__(self, messages):
        self._messages = list(messages)

    def __aiter__(self):
        self._iter = iter(self._messages)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class DummyChannel:
    def __init__(self, messages):
        self.id = 1511488659373691094
        self.name = "general"
        self._messages = {message.id: message for message in messages}
        self._history = list(messages)
        # Mirrors a real discord.py channel holding unpicklable state (asyncio
        # Futures); as_dict must not try to deep-copy the channel.
        self._unpicklable = threading.Lock()

    def history(self, limit: int = 20):
        return DummyHistory(self._history[:limit])

    def get_partial_message(self, message_id: int):
        message = self._messages.get(message_id)
        if message is None:
            raise RuntimeError("missing")
        return message

    async def delete_messages(self, messages):
        for message in messages:
            await message.delete()


@pytest.fixture
def discord_context():
    messages = [
        DummyMessage(30, DummyAuthor("mod", 3), "latest"),
        DummyMessage(20, DummyAuthor("alice", 2), "middle"),
        DummyMessage(10, DummyAuthor("bob", 1), "oldest"),
    ]
    channel = DummyChannel(messages)
    token = set_current_discord_context(
        DiscordRunContext(
            guild_id="1511488658350542878",
            guild_name="Test Guild",
            channel_id=str(channel.id),
            channel_name=channel.name,
            channel_kind="TextChannel",
            message_id="999",
            message_url="https://discord.example/message/999",
            message_text="delete the selected messages",
            user_id="1256065127401132129",
            username="__kharma__",
            channel=channel,
        )
    )
    try:
        yield channel
    finally:
        reset_current_discord_context(token)


def test_describe_current_discord_context_uses_live_ids(discord_context):
    result = describe_current_discord_context.entrypoint()
    assert "1511488658350542878" in _tool_text(result)
    assert "1511488659373691094" in _tool_text(result)
    assert "general" in _tool_text(result)


@pytest.mark.asyncio
async def test_list_recent_discord_messages_returns_concrete_message_ids(discord_context):
    result = await list_recent_discord_messages.entrypoint(limit=2)
    assert '"message_id": "20"' in _tool_text(result)
    assert '"message_id": "30"' in _tool_text(result)


@pytest.mark.asyncio
async def test_delete_discord_message_deletes_from_current_channel(discord_context):
    result = await delete_discord_message.entrypoint(message_id="20")
    assert "Deleted message 20" in _tool_text(result)
    assert discord_context._messages[20].deleted is True


@pytest.mark.asyncio
async def test_delete_discord_messages_bulk_deletes_each_id(discord_context):
    result = await delete_discord_messages.entrypoint(message_ids=["10", "30"])
    assert "Deleted 2 message(s)" in _tool_text(result)
    assert discord_context._messages[10].deleted is True
    assert discord_context._messages[30].deleted is True
    assert discord_context._messages[20].deleted is False


@pytest.mark.asyncio
async def test_delete_recent_discord_messages_skips_current_request_and_deletes_count(discord_context):
    result = await delete_recent_discord_messages.entrypoint(count=2)
    assert "Deleted 2 recent message(s)" in _tool_text(result)
    assert discord_context._messages[30].deleted is True
    assert discord_context._messages[20].deleted is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "kwargs"),
    [
        (delete_discord_message, {"message_id": "20"}),
        (delete_discord_messages, {"message_ids": ["10", "30"]}),
        (delete_recent_discord_messages, {"count": 2}),
    ],
)
async def test_delete_tools_refuse_when_request_is_not_a_delete(tool, kwargs):
    messages = [
        DummyMessage(30, DummyAuthor("mod", 3), "latest"),
        DummyMessage(20, DummyAuthor("alice", 2), "middle"),
        DummyMessage(10, DummyAuthor("bob", 1), "oldest"),
    ]
    channel = DummyChannel(messages)
    token = set_current_discord_context(
        DiscordRunContext(
            guild_id="1511488658350542878",
            guild_name="Test Guild",
            channel_id=str(channel.id),
            channel_name=channel.name,
            channel_kind="TextChannel",
            message_id="999",
            message_url="https://discord.example/message/999",
            message_text="rename this thread to release-notes",
            user_id="1256065127401132129",
            username="__kharma__",
            channel=channel,
        )
    )
    try:
        result = await tool.entrypoint(**kwargs)
    finally:
        reset_current_discord_context(token)

    assert "Refusing to delete Discord messages" in _tool_text(result)
    assert all(message.deleted is False for message in channel._messages.values())
