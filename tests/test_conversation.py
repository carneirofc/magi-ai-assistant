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
    ConversationMood,
    ConversationReasoning,
    ConversationReply,
    ConversationService,
    ConversationToolCall,
    ConversationToolResult,
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
    """Minimal MemoryManager stand-in recording what the service called.

    `store.identity` mirrors the real `IdentityStore` slice the service reads —
    the bot's context text + avatar bytes — so identity injection can be exercised
    (pass a fake `identity`) without touching disk. The default is an empty identity
    (no text, no picture), so unrelated tests see the bare input they expect.
    """

    def __init__(self, identity=None):
        self.assistant_turns = []
        self.store = SimpleNamespace(
            identity=identity
            or SimpleNamespace(context_text=lambda: "", avatar_bytes=lambda: None)
        )

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

    def context_stats(self):
        return {"est_tokens": 0, "sections": {}, "short_term_turns": 0}


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


async def test_identity_text_injected_without_feeding_the_avatar():
    """The identity text leads the context, but the avatar is NEVER force-fed as an
    inbound image (that reads as user content and derails the model); the model
    pulls the picture in on demand via its tools instead."""
    identity = SimpleNamespace(
        context_text=lambda: "# Your identity\nYour name is Alyssa.",
        avatar_bytes=lambda: (b"png-bytes", "image/png"),
    )
    response = SimpleNamespace(status="COMPLETED", content="ok", reasoning_content=None)
    runner = _FakeRunner(response)
    service = ConversationService(runner=runner, memory=_FakeMemory(identity=identity))

    await service.handle(user_id=1, session_id="s", text="hi")

    (call,) = runner.calls
    assert "Your name is Alyssa." in call["input"]
    assert "images" not in call  # no avatar fed into the run


async def test_inbound_images_pass_through_untouched():
    """The user's own attachments still reach the run — identity never adds to them."""
    from agno.media import Image

    identity = SimpleNamespace(
        context_text=lambda: "",
        avatar_bytes=lambda: (b"avatar", "image/png"),
    )
    response = SimpleNamespace(status="COMPLETED", content="ok", reasoning_content=None)
    runner = _FakeRunner(response)
    service = ConversationService(runner=runner, memory=_FakeMemory(identity=identity))

    user_img = Image(url="https://cdn.example/a.png")
    await service.handle(user_id=1, session_id="s", text="hi", media={"images": [user_img]})

    assert runner.calls[0]["images"] == [user_img]


# --- knowledge auto-injection ---------------------------------------------------
class _FakeKnowledge:
    """A knowledge searcher stand-in returning fixed hits and recording queries."""

    def __init__(self, hits):
        self._hits = hits
        self.queries = []

    def search(self, query, top_k):
        self.queries.append((query, top_k))
        return self._hits


async def test_knowledge_hits_folded_into_context():
    """When auto-injection is on, the top-k corpus hits for the message ride the
    run input up front (query = the user's message)."""
    hit = SimpleNamespace(source="handbook.md", doc_id="d1", text="the rule is X")
    response = SimpleNamespace(status="COMPLETED", content="ok", reasoning_content=None)
    runner = _FakeRunner(response)
    knowledge = _FakeKnowledge([hit])
    service = ConversationService(
        runner=runner, memory=_FakeMemory(), knowledge=knowledge, knowledge_top_k=3
    )

    await service.handle(user_id=1, session_id="s", text="what is the rule?")

    assert knowledge.queries == [("what is the rule?", 3)]
    (call,) = runner.calls
    assert "the rule is X" in call["input"]
    assert "handbook.md" in call["input"]  # source labels the chunk


async def test_knowledge_not_searched_when_auto_injection_off():
    """top_k <= 0 means tool-only retrieval — the searcher is never touched and the
    input stays bare."""

    class _BoomKnowledge:
        def search(self, query, top_k):
            raise AssertionError("must not search when auto-injection is off")

    response = SimpleNamespace(status="COMPLETED", content="ok", reasoning_content=None)
    runner = _FakeRunner(response)
    service = ConversationService(
        runner=runner, memory=_FakeMemory(), knowledge=_BoomKnowledge(), knowledge_top_k=0
    )

    await service.handle(user_id=1, session_id="s", text="hi")

    assert runner.calls[0]["input"] == "hi"


async def test_knowledge_retrieval_failure_never_breaks_the_turn():
    """A searcher that raises is swallowed — the turn proceeds with no knowledge block."""

    class _BoomKnowledge:
        def search(self, query, top_k):
            raise RuntimeError("qdrant down")

    response = SimpleNamespace(status="COMPLETED", content="ok", reasoning_content=None)
    runner = _FakeRunner(response)
    service = ConversationService(
        runner=runner, memory=_FakeMemory(), knowledge=_BoomKnowledge(), knowledge_top_k=3
    )

    reply = await service.handle(user_id=1, session_id="s", text="hi")

    assert reply.text == "ok"
    assert runner.calls[0]["input"] == "hi"  # no knowledge block, turn unbroken


def test_context_stats_reports_knowledge_auto_injection():
    """`!ctx` surfaces the knowledge knob + an upper-bound token budget when
    auto-injection is on (actual per-turn size is query-dependent, logged at run time)."""
    service = ConversationService(
        runner=_FakeRunner(None), memory=_FakeMemory(),
        knowledge=_FakeKnowledge([]), knowledge_top_k=3,
    )

    stats = service.context_stats(user_id=1, session_id="s")

    assert stats["knowledge"]["auto_inject"] is True
    assert stats["knowledge"]["top_k"] == 3
    assert stats["knowledge"]["est_max_tokens"] > 0


def test_context_stats_knowledge_off_without_searcher():
    service = ConversationService(runner=_FakeRunner(None), memory=_FakeMemory())

    stats = service.context_stats(user_id=1, session_id="s")

    assert stats["knowledge"]["auto_inject"] is False
    assert stats["knowledge"]["est_max_tokens"] == 0


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


def _reasoning(text):
    """A fake reasoning-delta event (`event` ends with 'ReasoningContentDelta')."""
    return SimpleNamespace(event="TeamReasoningContentDelta", reasoning_content=text)


def _tool_exec(call_id, name, args=None, result=None, error=False):
    """A fake agno `ToolExecution` payload carried by tool events."""
    return SimpleNamespace(
        tool_call_id=call_id,
        tool_name=name,
        tool_args=args or {},
        result=result,
        tool_call_error=error,
    )


def _tool_started(tool):
    return SimpleNamespace(event="TeamToolCallStarted", tool=tool)


def _tool_completed(tool):
    return SimpleNamespace(event="TeamToolCallCompleted", tool=tool)


def _tool_errored(tool):
    return SimpleNamespace(event="TeamToolCallError", tool=tool)


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


async def test_stream_surfaces_reasoning_and_tool_events():
    """Reasoning deltas and tool start/result events ride the stream as their own
    observability items, interleaved with text; the final reply is unchanged."""
    tool = _tool_exec("call-1", "web_search", {"q": "cats"}, result="found")
    runner = _FakeStreamRunner(
        [
            _reasoning("let me think"),
            _tool_started(tool),
            _tool_completed(tool),
            _delta("the "),
            _delta("answer"),
            _final(content="the answer"),
        ]
    )
    service = ConversationService(runner=runner, memory=_FakeMemory())

    items = await _collect(service)

    assert items[0] == ConversationReasoning(text="let me think")
    assert items[1] == ConversationToolCall(call_id="call-1", name="web_search", args={"q": "cats"})
    assert items[2] == ConversationToolResult(call_id="call-1", result="found", is_error=False)
    assert items[3:5] == [ConversationDelta(text="the "), ConversationDelta(text="answer")]
    assert items[-1] == ConversationReply(text="the answer")


async def test_stream_tool_error_event_is_flagged():
    tool = _tool_exec("c2", "flaky", result="ERROR: boom", error=True)
    runner = _FakeStreamRunner([_tool_started(tool), _tool_errored(tool), _final(content="ok")])
    service = ConversationService(runner=runner, memory=_FakeMemory())

    items = await _collect(service)

    assert items[0] == ConversationToolCall(call_id="c2", name="flaky", args={})
    assert items[1] == ConversationToolResult(call_id="c2", result="ERROR: boom", is_error=True)


# --- mood pass (pre-reply; magi/agent/mood is injected as mood_fn) ------------
async def _wry_mood(run_input: str) -> str:
    return "wry"


async def test_stream_mood_event_arrives_before_the_first_delta():
    runner = _FakeStreamRunner([_delta("a"), _delta("b"), _final(content="ab")])
    service = ConversationService(runner=runner, memory=_FakeMemory(), mood_fn=_wry_mood)

    items = await _collect(service)

    assert items[0] == ConversationMood(mood="wry")
    assert items[1] == ConversationDelta(text="a")
    assert items[-1].mood == "wry"  # the same value rides the final reply


async def test_stream_without_mood_fn_emits_no_mood_event():
    runner = _FakeStreamRunner([_delta("a"), _final(content="a")])
    service = ConversationService(runner=runner, memory=_FakeMemory())

    items = await _collect(service)

    assert not any(isinstance(item, ConversationMood) for item in items)
    assert items[-1].mood is None


async def test_stream_mood_failure_falls_back_and_never_breaks_the_turn():
    async def broken(run_input: str) -> str:
        raise RuntimeError("pass down")

    runner = _FakeStreamRunner([_delta("a"), _final(content="a")])
    service = ConversationService(runner=runner, memory=_FakeMemory(), mood_fn=broken)

    items = await _collect(service)

    # Fallback = the vocabulary's first entry (the engine default starts neutral).
    assert items[0] == ConversationMood(mood="neutral")
    assert items[-1].text == "a"


async def test_handle_reply_carries_mood():
    response = SimpleNamespace(status="COMPLETED", content="the answer", reasoning_content=None)
    mem = _FakeMemory()
    service = ConversationService(
        runner=_FakeRunner(response), memory=mem, mood_fn=_wry_mood
    )

    reply = await service.handle(user_id=1, session_id="s", text="hi")

    assert reply.text == "the answer"
    assert reply.mood == "wry"


async def test_handle_without_mood_fn_leaves_mood_none():
    response = SimpleNamespace(status="COMPLETED", content="ok", reasoning_content=None)
    service, _ = _service(response)

    reply = await service.handle(user_id=1, session_id="s", text="hi")

    assert reply.mood is None


# --- greeting (assistant-initiated turn; greet_stream) ------------------------
async def _collect_greeting(service, instruction="Say hello."):
    return [
        item
        async for item in service.greet_stream(
            user_id=1, session_id="s", instruction=instruction
        )
    ]


async def test_greeting_streams_like_a_turn_and_records_only_the_assistant():
    runner = _FakeStreamRunner([_delta("hey"), _final(content="hey")])
    mem = _FakeMemory()
    mem.user_turns = []
    mem.record_user_turn = lambda text: mem.user_turns.append(text)
    service = ConversationService(runner=runner, memory=mem, mood_fn=_wry_mood)

    items = await _collect_greeting(service)

    assert items[0] == ConversationMood(mood="wry")  # mood frame included
    assert items[1] == ConversationDelta(text="hey")
    assert items[-1].text == "hey" and items[-1].mood == "wry"
    assert mem.user_turns == []  # no user message recorded
    assert mem.assistant_turns == ["hey"]  # the greeting lands in history


async def test_greeting_skips_the_curator():
    runner = _FakeStreamRunner([_delta("hi"), _final(content="hi")])
    mem = _FakeMemory()
    service = ConversationService(runner=runner, memory=mem)

    await _collect_greeting(service)

    assert not hasattr(mem, "curated")  # maybe_curate never called


async def test_greeting_instruction_rides_the_run_input_with_context():
    class _RecordingStreamRunner(_FakeStreamRunner):
        def arun(self, **kwargs):
            self.kwargs = kwargs
            return super().arun(**kwargs)

    runner = _RecordingStreamRunner([_final(content="hello there")])
    identity = SimpleNamespace(
        context_text=lambda: "# Your identity\nYour name is Alyssa.",
        avatar_bytes=lambda: None,
    )
    service = ConversationService(runner=runner, memory=_FakeMemory(identity=identity))

    await _collect_greeting(service, instruction="Open the conversation warmly.")

    run_input = runner.kwargs["input"]
    assert "Open the conversation warmly." in run_input
    assert "Your name is Alyssa." in run_input  # context assembled like a turn


async def test_greeting_error_still_ends_with_a_reply():
    runner = _FakeStreamRunner([_delta("a")], raise_after=0)
    service = ConversationService(runner=runner, memory=_FakeMemory())

    items = await _collect_greeting(service)

    assert items[-1] == ConversationReply(text=_ERROR_REPLY, is_error=True)
