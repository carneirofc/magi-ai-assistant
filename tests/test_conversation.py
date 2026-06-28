"""Tests for ConversationService reply handling (core.conversation).

Focus: the service must never hand the channel silence. A run that finishes
cleanly but with no content (the lead going quiet after a tool error) gets an
honest fallback, while an ERROR status gets the error reply — and a normal reply
is recorded to memory and passed through untouched.
"""

from types import SimpleNamespace

from magi.core.conversation import (
    _EMPTY_REPLY,
    _ERROR_REPLY,
    ConversationDelta,
    ConversationReply,
    ConversationService,
    _inbound_media_urls,
)


def test_inbound_media_urls_extracts_by_reference_urls_only():
    by_ref = SimpleNamespace(url="https://cdn.example/a.png")
    by_bytes = SimpleNamespace(url=None)  # inline-byte image: already visible
    media = {"images": [by_ref, by_bytes], "videos": [], "audio": []}
    assert _inbound_media_urls(media) == ["https://cdn.example/a.png"]


def test_inbound_media_urls_empty_for_no_media():
    assert _inbound_media_urls({}) == []


class _FakeMemory:
    """Minimal MemoryManager stand-in recording what the service called."""

    def __init__(self):
        self.assistant_turns = []

    def set_scope(self, user_id, session_id):
        pass

    def build_context(self, query):
        return ""

    def record_user_turn(self, text):
        pass

    def record_assistant_turn(self, text):
        self.assistant_turns.append(text)

    async def maybe_summarize_session(self):
        pass

    async def maybe_curate(self, user_message, assistant_reply):
        self.curated = (user_message, assistant_reply)


class _FakeRunner:
    def __init__(self, response):
        self._response = response
        self.calls = []

    async def arun(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


def _service(response):
    mem = _FakeMemory()
    return ConversationService(runner=_FakeRunner(response), memory=mem), mem


async def test_context_rides_in_the_run_input_not_on_the_runner():
    """Per-run context must travel inside `input` (a shared runner attribute would
    race concurrent conversations and leak one user's memory into another's run)."""
    response = SimpleNamespace(status="COMPLETED", content="ok", reasoning_content=None)
    runner = _FakeRunner(response)
    service = ConversationService(
        runner=runner, memory=_FakeMemory(), channel_guidance="channel rules"
    )

    await service.handle(user_id=1, session_id="s", text="hi", extra_context="who/where")

    assert not hasattr(runner, "additional_context")  # nothing set on the shared runner
    (call,) = runner.calls
    assert call["user_id"] == "1"  # normalized for agno (expects str)
    assert call["input"].endswith("hi")
    assert "who/where" in call["input"]
    assert "channel rules" in call["input"]


async def test_no_context_means_bare_input():
    response = SimpleNamespace(status="COMPLETED", content="ok", reasoning_content=None)
    runner = _FakeRunner(response)
    service = ConversationService(runner=runner, memory=_FakeMemory())

    await service.handle(user_id=1, session_id="s", text="hi")

    assert runner.calls[0]["input"] == "hi"


async def test_completed_but_empty_returns_fallback():
    # The logged failure: tool errored, lead completed with no text and no reasoning.
    response = SimpleNamespace(status="COMPLETED", content="", reasoning_content=None)
    service, mem = _service(response)

    reply = await service.handle(user_id=1, session_id="s", text="hi")

    assert reply.text == _EMPTY_REPLY
    assert reply.is_error is True
    assert mem.assistant_turns == []  # nothing real to record


async def test_error_status_returns_error_reply():
    response = SimpleNamespace(status="ERROR", content="boom", reasoning_content=None)
    service, _ = _service(response)

    reply = await service.handle(user_id=1, session_id="s", text="hi")

    assert reply.text == _ERROR_REPLY
    assert reply.is_error is True


async def test_normal_reply_is_recorded_and_passed_through():
    response = SimpleNamespace(status="COMPLETED", content="the answer", reasoning_content=None)
    service, mem = _service(response)

    reply = await service.handle(user_id=1, session_id="s", text="hi")

    assert reply.text == "the answer"
    assert reply.is_error is False
    assert mem.assistant_turns == ["the answer"]


async def test_reasoning_only_is_not_overridden_by_fallback():
    # Empty text but reasoning present: send reasoning, don't fire the fallback.
    response = SimpleNamespace(status="COMPLETED", content="", reasoning_content="thinking…")
    service, _ = _service(response)

    reply = await service.handle(user_id=1, session_id="s", text="hi")

    assert reply.text == ""
    assert reply.reasoning == "thinking…"
    assert reply.is_error is False


# --- reply media ---------------------------------------------------------------
async def test_run_output_media_rides_the_reply_minus_view_only():
    from agno.media import Image

    from magi.core.media import view_only_id

    viewed = Image(id=view_only_id(), content=b"viewed")
    delivered = Image(content=b"delivered")
    response = SimpleNamespace(
        status="COMPLETED",
        content="here",
        reasoning_content=None,
        images=[viewed, delivered],
    )
    service, _ = _service(response)

    reply = await service.handle(user_id=1, session_id="s", text="hi")

    assert [i.content for i in reply.images] == [b"delivered"]
    assert reply.has_media


async def test_outbox_media_staged_during_run_rides_the_reply():
    """A tool staging media mid-run (send_media_from_url) must reach the reply."""
    from agno.media import Audio

    from magi.core.media import stage_media

    class _StagingRunner:
        async def arun(self, **kwargs):
            assert stage_media(audio=(Audio(content=b"wav", format="wav"),)) is True
            return SimpleNamespace(status="COMPLETED", content="done", reasoning_content=None)

    service = ConversationService(runner=_StagingRunner(), memory=_FakeMemory())

    reply = await service.handle(user_id=1, session_id="s", text="hi")

    assert len(reply.audio) == 1 and reply.audio[0].content == b"wav"


async def test_media_only_reply_is_not_replaced_by_fallback():
    from agno.media import Image

    response = SimpleNamespace(
        status="COMPLETED", content="", reasoning_content=None, images=[Image(content=b"x")]
    )
    service, _ = _service(response)

    reply = await service.handle(user_id=1, session_id="s", text="hi")

    assert reply.text == "" and reply.is_error is False
    assert len(reply.images) == 1


# --- streaming (handle_stream) ----------------------------------------------
def _delta(text):
    """A fake agno content-delta event (`event` ends with 'RunContent')."""
    return SimpleNamespace(event="TeamRunContent", content=text)


def _final(status="COMPLETED", content=None, reasoning=None):
    """A fake final RunOutput (carries `status`, no `event`)."""
    return SimpleNamespace(status=status, content=content, reasoning_content=reasoning)


class _FakeStreamRunner:
    """agno-shaped streaming runner: `arun` is sync and returns the iterator."""

    def __init__(self, events, raise_after=None):
        self._events = events
        self._raise_after = raise_after

    def arun(self, **kwargs):
        async def stream():
            for i, event in enumerate(self._events):
                if self._raise_after is not None and i >= self._raise_after:
                    raise RuntimeError("stream broke")
                yield event

        return stream()


async def _collect(service):
    return [
        item
        async for item in service.handle_stream(user_id=1, session_id="s", text="hi")
    ]


async def test_stream_yields_deltas_then_final_reply_and_records_once():
    runner = _FakeStreamRunner([_delta("a"), _delta("b"), _final(content="ab")])
    mem = _FakeMemory()
    service = ConversationService(runner=runner, memory=mem)

    items = await _collect(service)

    assert items[:2] == [ConversationDelta(text="a"), ConversationDelta(text="b")]
    assert items[2] == ConversationReply(text="ab")
    assert mem.assistant_turns == ["ab"]  # recorded once, from the final text


async def test_stream_falls_back_to_joined_deltas_when_final_has_no_content():
    runner = _FakeStreamRunner([_delta("a"), _delta("b"), _final(content=None)])
    mem = _FakeMemory()
    service = ConversationService(runner=runner, memory=mem)

    items = await _collect(service)

    assert items[-1].text == "ab"
    assert mem.assistant_turns == ["ab"]


async def test_stream_error_status_ends_with_error_reply():
    runner = _FakeStreamRunner([_delta("a"), _final(status="ERROR", content="boom")])
    mem = _FakeMemory()
    service = ConversationService(runner=runner, memory=mem)

    items = await _collect(service)

    assert items[-1] == ConversationReply(text=_ERROR_REPLY, is_error=True)
    assert mem.assistant_turns == []


async def test_stream_exception_ends_with_error_reply():
    runner = _FakeStreamRunner([_delta("a"), _delta("b")], raise_after=1)
    mem = _FakeMemory()
    service = ConversationService(runner=runner, memory=mem)

    items = await _collect(service)

    assert items[0] == ConversationDelta(text="a")
    assert items[-1] == ConversationReply(text=_ERROR_REPLY, is_error=True)
    assert mem.assistant_turns == []


async def test_stream_empty_run_ends_with_honest_fallback():
    runner = _FakeStreamRunner([_final(content=None)])
    service = ConversationService(runner=runner, memory=_FakeMemory())

    items = await _collect(service)

    assert items == [ConversationReply(text=_EMPTY_REPLY, is_error=True)]
