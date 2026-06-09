"""Tests for ConversationService reply handling (core.conversation).

Focus: the service must never hand the channel silence. A run that finishes
cleanly but with no content (the lead going quiet after a tool error) gets an
honest fallback, while an ERROR status gets the error reply — and a normal reply
is recorded to memory and passed through untouched.
"""

from types import SimpleNamespace

from core.conversation import _EMPTY_REPLY, _ERROR_REPLY, ConversationService


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

    async def maybe_summarize_long_term(self):
        pass


class _FakeRunner:
    def __init__(self, response):
        self._response = response
        self.additional_context = ""

    async def arun(self, **kwargs):
        return self._response


def _service(response):
    mem = _FakeMemory()
    return ConversationService(runner=_FakeRunner(response), memory=mem), mem


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
