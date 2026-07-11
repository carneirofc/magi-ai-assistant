"""Channel-agnostic conversation orchestration.

Owns the run + memory flow for one inbound message, free of any channel concern
(no `discord` import, no formatting): scope the memory, assemble context, record
the turn, run the magi/agent/team, record the reply, and fold summaries. Channels feed
plain inputs in and render the plain `ConversationReply` out.

`runner` is an agno `Agent` or `Team`; `memory` is a `MemoryManager`. Both are
injected — nothing is constructed here.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional, Protocol

from agno.utils.log import log_error, log_info, log_warning
from agno.utils.message import get_text_from_message

from magi.core.config import config
from magi.core.media import (
    close_allowed_media_urls,
    close_media_outbox,
    collect_reply_media,
    open_allowed_media_urls,
    open_media_outbox,
)
from magi.core.memory import MemoryManager

if TYPE_CHECKING:
    from magi.core.knowledge import KnowledgeSearcher

# Rough provider-agnostic token estimate (~4 chars/token), matching the memory
# layer's heuristic (magi/core/memory/manager). Observability only — never truncates.
_CHARS_PER_TOKEN = 4


def _est_tokens_from_chars(chars: int) -> int:
    return (chars + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN


class Runner(Protocol):
    """The slice of an agno `Agent`/`Team` this service drives: one run, awaited
    whole or consumed as an event stream (`stream=True` rides through kwargs)."""

    def arun(
        self, *, input: str, user_id: str, session_id: str, **kwargs: Any
    ) -> Any: ...


# The pre-reply mood pass (magi/agent/mood): assembled run input -> one name from
# config.mood_vocabulary. Injected like the summarizers/curator so this module
# stays model-free; contractually it never raises and never returns free text —
# the service still guards, because a broken pass must not break a turn.
MoodFn = Callable[[str], Awaitable[str]]

def _inbound_media_urls(media: dict[str, Any]) -> list[str]:
    """The http(s) URLs of inbound media passed by reference (not inline bytes).

    These let `view_image_from_url` fetch an image the user attached as a link
    rather than typed into the text, while still rejecting URLs the model
    invented (which appear on no inbound media object). Inline-byte media has no
    URL and is already visible to the model, so it contributes nothing here.
    """
    urls: list[str] = []
    for items in media.values():
        for item in items or ():
            url = getattr(item, "url", None)
            if url:
                urls.append(url)
    return urls


def _tool_call_event(tool: Any) -> Optional["ConversationToolCall"]:
    """A `ConversationToolCall` from an agno `ToolExecution`, or None if there's
    no usable tool payload (defensive: the event's `tool` is Optional upstream)."""
    if tool is None:
        return None
    name = getattr(tool, "tool_name", None)
    if not name:
        return None
    args = getattr(tool, "tool_args", None)
    return ConversationToolCall(
        call_id=getattr(tool, "tool_call_id", None) or "",
        name=name,
        args=args if isinstance(args, dict) else {},
    )


def _tool_result_event(tool: Any) -> Optional["ConversationToolResult"]:
    """A `ConversationToolResult` from an agno `ToolExecution`, or None when the
    tool payload is missing."""
    if tool is None:
        return None
    result = getattr(tool, "result", None)
    return ConversationToolResult(
        call_id=getattr(tool, "tool_call_id", None) or "",
        result=result if isinstance(result, str) else ("" if result is None else str(result)),
        is_error=bool(getattr(tool, "tool_call_error", False)),
    )


_ERROR_REPLY = "Sorry, there was an error processing your message. Please try again later."
# Run finished cleanly but the lead emitted nothing — e.g. a tool failed and it
# stalled instead of recovering. Never hand the channel silence: say so honestly.
_EMPTY_REPLY = (
    "I wasn't able to put together an answer for that — a step I tried didn't pan out. "
    "Mind rephrasing or asking again?"
)


@dataclass(frozen=True)
class ConversationUsage:
    """Token accounting for one handled turn, aggregated over the run.

    Read from the agno run output's `metrics` (a `RunMetrics`), which sums the
    lead's model calls (and, for a team, its members'). `context_window` is the
    lead's configured window, so a channel can render "how full is the context".
    All counts are best-effort — some backends under-report — so treat them as
    observability, never as a hard budget.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    context_window: Optional[int] = None


@dataclass(frozen=True)
class ConversationReply:
    """The result of handling one message, in channel-neutral terms.

    Media tuples hold agno media objects (`agno.media.Image` etc.) gathered
    from the run output and the per-run outbox (see magi/core/media.py); each
    channel renders them its own way (Discord uploads attachments, the API
    serializes them).
    """

    text: str
    reasoning: Optional[str] = None
    is_error: bool = False
    images: tuple = ()
    videos: tuple = ()
    audio: tuple = ()
    files: tuple = ()
    usage: Optional[ConversationUsage] = None
    # The turn's delivery mood (one config.mood_vocabulary name), predicted before
    # the reply by the mood pass. None when the pass is off or the turn errored.
    # Not display-only: this is the future TTS style input.
    mood: Optional[str] = None

    @property
    def has_media(self) -> bool:
        return bool(self.images or self.videos or self.audio or self.files)


@dataclass(frozen=True)
class ConversationDelta:
    """One streamed chunk of the reply text (see `handle_stream`)."""

    text: str


@dataclass(frozen=True)
class ConversationReasoning:
    """One streamed chunk of the model's thinking (see `handle_stream`).

    Emitted only when the run produces reasoning; many turns produce none. It is
    observability, not the answer — the final `ConversationReply` remains
    authoritative for what to persist.
    """

    text: str


@dataclass(frozen=True)
class ConversationToolCall:
    """A tool the lead just started calling, surfaced live for observability.

    `call_id` ties a call to its later `ConversationToolResult`; `name` is the
    tool/function name and `args` the arguments it was invoked with.
    """

    call_id: str
    name: str
    args: dict


@dataclass(frozen=True)
class ConversationToolResult:
    """The outcome of a previously started tool call (matched by `call_id`)."""

    call_id: str
    result: str
    is_error: bool = False


@dataclass(frozen=True)
class ConversationMood:
    """The turn's predicted delivery mood, yielded before the first delta.

    Emitted only when the mood pass is wired (config.mood_enabled); arrives ahead
    of the reply so a client can react (change the avatar's face) as the answer
    starts streaming. The same value rides the final reply's `mood` field.
    """

    mood: str


# The streamed items `handle_stream` yields before its terminal reply: text
# deltas plus the live mood/reasoning/tool observability events.
ConversationStreamEvent = (
    ConversationDelta
    | ConversationReasoning
    | ConversationToolCall
    | ConversationToolResult
    | ConversationMood
)


class ConversationService:
    def __init__(
        self,
        runner: Runner,
        memory: MemoryManager,
        channel_guidance: str = "",
        context_window: Optional[int] = None,
        knowledge: Optional["KnowledgeSearcher"] = None,
        knowledge_top_k: int = 0,
        mood_fn: Optional[MoodFn] = None,
    ):
        self.runner = runner
        self.memory = memory
        # The pre-reply mood pass (see `MoodFn`). None = feature off: no mood
        # events, `reply.mood` stays None.
        self.mood_fn = mood_fn
        # Channel-specific output rules (e.g. Discord markdown). Appended to the
        # run context so the base prompt stays channel-agnostic.
        self.channel_guidance = channel_guidance
        # The lead's context window (tokens), so replies can report how full it
        # is. Purely informational; None when the channel doesn't wire it.
        self.context_window = context_window
        # The knowledge RAG corpus (magi/core/knowledge) and how many hits to fold
        # into each run's context. Distinct from memory: a global, read-only
        # reference the model can also search on demand via its tool. Off (no
        # auto-injection) when the searcher is None or top_k <= 0.
        self.knowledge = knowledge
        self.knowledge_top_k = knowledge_top_k

    def _usage_from(self, run_output: object) -> Optional[ConversationUsage]:
        """Lift agno's `RunMetrics` off a run output into channel-neutral usage.

        Returns None when the output carries no metrics (some backends omit
        them). Missing token fields default to 0 so the shape stays stable.
        """
        metrics = getattr(run_output, "metrics", None)
        if metrics is None:
            return None
        return ConversationUsage(
            input_tokens=int(getattr(metrics, "input_tokens", 0) or 0),
            output_tokens=int(getattr(metrics, "output_tokens", 0) or 0),
            total_tokens=int(getattr(metrics, "total_tokens", 0) or 0),
            cached_tokens=int(getattr(metrics, "cache_read_tokens", 0) or 0),
            reasoning_tokens=int(getattr(metrics, "reasoning_tokens", 0) or 0),
            context_window=self.context_window,
        )

    def _knowledge_context(self, query: str) -> str:
        """Top-k knowledge-corpus hits for this message, as a context block.

        The corpus (magi/core/knowledge) is a global, read-only reference the model
        can also search on demand via its tool; this surfaces the most relevant
        chunks up front so it doesn't have to ask. Empty when auto-injection is off
        (no searcher / top_k <= 0), the query is blank, or nothing matches. Never
        raises — a retrieval hiccup must not break a turn.
        """
        if self.knowledge is None or self.knowledge_top_k <= 0 or not query.strip():
            return ""
        try:
            hits = self.knowledge.search(query, self.knowledge_top_k)
        except Exception as exc:  # noqa: BLE001 — retrieval must never break a chat.
            log_warning(f"conversation: knowledge retrieval failed: {type(exc).__name__}: {exc}")
            return ""
        if not hits:
            return ""
        lines = ["# Knowledge (reference corpus — retrieved for this message)"]
        for h in hits:
            label = h.source or h.doc_id or "source"
            lines.append(f"- ({label}) {h.text}")
        block = "\n".join(lines)
        # Surface the knowledge contribution alongside the memory layer's own
        # context-size log (magi/core/memory), so the per-turn accounting is complete.
        log_info(f"conversation: knowledge context ~{_est_tokens_from_chars(len(block))} tok ({len(hits)} hit(s))")
        return block

    def _prepare_input(
        self, user_id: str, session_id: str, text: str, extra_context: str
    ) -> str:
        """Scope memory, record the inbound turn, and build this run's input.

        The context rides inside the run's input, never on the shared runner:
        mutating `runner.additional_context` races concurrent conversations (the
        team reads it mid-run, so one user could see another's memory).
        """
        self.memory.set_scope(user_id, session_id)
        # Assemble context in order: who the bot is (its identity), caller/channel
        # info, persisted memory, retrieved knowledge, then channel output rules.
        # The message is the retrieval query for both memory and knowledge.
        parts = [
            self.memory.store.identity.context_text(),
            extra_context,
            self.memory.build_context(query=text),
            self._knowledge_context(text),
            self.channel_guidance,
        ]
        context = "\n\n".join(p for p in parts if p and p.strip())
        self.memory.record_user_turn(text)
        return f"<context>\n{context}\n</context>\n\n{text}" if context else text

    async def _turn_mood(self, run_input: str) -> Optional[str]:
        """This turn's delivery mood via the injected pass, or None when off.

        The pass contractually never raises, but a turn must survive a broken one:
        any failure degrades to the vocabulary's first entry (the same fallback
        the pass itself uses) rather than dropping the signal mid-conversation.
        """
        if self.mood_fn is None:
            return None
        try:
            return await self.mood_fn(run_input)
        except Exception as exc:  # noqa: BLE001 — mood must never break a turn.
            log_warning(f"conversation: mood pass failed: {type(exc).__name__}: {exc}")
            return next(iter(config.mood_vocabulary), None)

    async def _finish_turn(
        self,
        reply: str,
        reasoning: Optional[str],
        media: Optional[dict] = None,
        user_text: str = "",
        usage: Optional[ConversationUsage] = None,
        mood: Optional[str] = None,
    ) -> ConversationReply:
        """Record the reply + fold/curate memory; the one tail both run modes share."""
        media = media or {}
        if reply:
            self.memory.record_assistant_turn(reply)
            # Fold rolled-off turns (no-op unless enabled), then let the post-turn
            # curator revise durable memory from this turn (no-op unless enabled).
            await self.memory.maybe_summarize_session()
            await self.memory.maybe_curate(user_text, reply)
        elif not reasoning and not any(media.values()):
            # Completed with neither answer, reasoning, nor media: the lead went
            # silent (commonly after a tool error). Return an honest fallback
            # instead of an empty string so the channel never has to invent one.
            log_warning("run completed with no content; returning fallback")
            return ConversationReply(text=_EMPTY_REPLY, is_error=True, mood=mood)
        return ConversationReply(
            text=reply, reasoning=reasoning, is_error=False, usage=usage, mood=mood, **media
        )

    async def handle(
        self,
        *,
        user_id: str | int,
        session_id: str,
        text: str,
        media: Optional[dict] = None,
        extra_context: str = "",
    ) -> ConversationReply:
        """Run one turn end to end and return a channel-neutral reply."""
        media = media or {}
        user_id = str(user_id)
        log_info(f"conversation: handling (session={session_id}, user={user_id})")

        run_input = self._prepare_input(user_id, session_id, text, extra_context)
        # Predict the delivery mood before the reply (see MoodFn); it rides the
        # final reply so non-streaming clients (and later TTS) get it too.
        mood = await self._turn_mood(run_input)
        allowed_urls_token = open_allowed_media_urls(text, _inbound_media_urls(media))
        outbox_token = open_media_outbox()
        try:
            response = await self.runner.arun(
                input=run_input, user_id=user_id, session_id=session_id, **media
            )
        finally:
            outbox = close_media_outbox(outbox_token)
            close_allowed_media_urls(allowed_urls_token)
        log_info(
            f"conversation: status={response.status}, "
            f"content_len={len(response.content or '')}"
        )

        if response.status == "ERROR":
            log_error(response.content)
            return ConversationReply(text=_ERROR_REPLY, is_error=True)

        reply = get_text_from_message(response.content) if response.content else ""
        return await self._finish_turn(
            reply,
            getattr(response, "reasoning_content", None),
            collect_reply_media(response, outbox),
            user_text=text,
            usage=self._usage_from(response),
            mood=mood,
        )

    async def handle_stream(
        self,
        *,
        user_id: str | int,
        session_id: str,
        text: str,
        media: Optional[dict] = None,
        extra_context: str = "",
    ) -> AsyncIterator[ConversationStreamEvent | ConversationReply]:
        """Like `handle`, but yields the reply incrementally.

        Yields, as the run unfolds: a `ConversationMood` first (when the mood pass
        is wired — before any delta, so a client can react as the answer starts),
        then a `ConversationDelta` per text chunk, plus live observability events —
        `ConversationReasoning` (thinking chunks), `ConversationToolCall`/
        `ConversationToolResult` (the lead's tool activity) — then exactly one
        final `ConversationReply` (the authoritative result — channels should
        render it over the assembled deltas). Memory semantics are identical to
        `handle`: the turn is recorded and folded once, from the final text. A
        channel that only wants text can ignore the non-delta events.
        """
        media = media or {}
        user_id = str(user_id)
        log_info(f"conversation: streaming (session={session_id}, user={user_id})")

        run_input = self._prepare_input(user_id, session_id, text, extra_context)
        # The pre-reply mood pass gates the stream start on purpose: the mood must
        # arrive before the first content token (that's its whole point).
        mood = await self._turn_mood(run_input)
        if mood is not None:
            yield ConversationMood(mood=mood)
        chunks: list[str] = []
        final = None
        allowed_urls_token = open_allowed_media_urls(text, _inbound_media_urls(media))
        outbox_token = open_media_outbox()
        try:
            stream = self.runner.arun(
                input=run_input,
                user_id=user_id,
                session_id=session_id,
                stream=True,
                # Emit the lead's intermediate events (reasoning + tool calls), not
                # just content, so channels can show live thinking/tool activity.
                # Member events stay off (team `stream_member_events=False`), so this
                # is the lead's own activity only, not every delegate's.
                stream_events=True,
                # agno yields the full RunOutput as the stream's last item; that is
                # the same object the non-stream path gets, so both finish alike.
                yield_run_output=True,
                **media,
            )
            async for event in stream:
                if hasattr(event, "status"):  # the final RunOutput/TeamRunOutput
                    final = event
                    continue
                # Event names are suffix-matched so the agent (`RunContent`) and
                # team (`TeamRunContent`) variants both hit the same branch.
                name = getattr(event, "event", "")
                # Content deltas: `RunContent` / `TeamRunContent`.
                if name.endswith("RunContent"):
                    delta = getattr(event, "content", None)
                    if isinstance(delta, str) and delta:
                        chunks.append(delta)
                        yield ConversationDelta(text=delta)
                # Thinking deltas: `ReasoningContentDelta` / `TeamReasoningContentDelta`.
                elif name.endswith("ReasoningContentDelta"):
                    reasoning_chunk = getattr(event, "reasoning_content", None)
                    if isinstance(reasoning_chunk, str) and reasoning_chunk:
                        yield ConversationReasoning(text=reasoning_chunk)
                # Tool started: `ToolCallStarted` / `TeamToolCallStarted`.
                elif name.endswith("ToolCallStarted"):
                    call = _tool_call_event(getattr(event, "tool", None))
                    if call is not None:
                        yield call
                # Tool finished (ok or error): `ToolCall(Completed|Error)` variants.
                elif name.endswith("ToolCallCompleted") or name.endswith("ToolCallError"):
                    result = _tool_result_event(getattr(event, "tool", None))
                    if result is not None:
                        yield result
        except Exception as exc:  # noqa: BLE001 — the stream must end with a reply.
            log_error(f"conversation: stream failed: {type(exc).__name__}: {exc}")
            yield ConversationReply(text=_ERROR_REPLY, is_error=True)
            return
        finally:
            outbox = close_media_outbox(outbox_token)
            close_allowed_media_urls(allowed_urls_token)

        if final is not None and final.status == "ERROR":
            log_error(final.content)
            yield ConversationReply(text=_ERROR_REPLY, is_error=True)
            return

        # Prefer the run's own final content (authoritative); fall back to the
        # concatenated deltas if the output carried none.
        reply = ""
        if final is not None and final.content:
            reply = get_text_from_message(final.content)
        reply = reply or "".join(chunks)
        reasoning = getattr(final, "reasoning_content", None) if final is not None else None
        yield await self._finish_turn(
            reply,
            reasoning,
            collect_reply_media(final, outbox),
            user_text=text,
            usage=self._usage_from(final) if final is not None else None,
            mood=mood,
        )

    # --- control commands (channel formats the reply text) ------------------
    def flush(self, user_id: str | int, session_id: str) -> int:
        """Close the session (fold summary → episode, wipe live turns). Returns dropped."""
        self.memory.set_scope(user_id, session_id)
        return self.memory.flush_session()

    def context_stats(self, user_id: str | int, session_id: str) -> dict:
        self.memory.set_scope(user_id, session_id)
        stats = self.memory.context_stats()
        stats["knowledge"] = self._knowledge_stats()
        return stats

    def _knowledge_stats(self) -> dict:
        """Knowledge auto-injection accounting for `!ctx`.

        The real per-turn size is query-dependent (retrieved per message; logged
        then — see `_knowledge_context`), so here we report the knob plus a rough
        upper bound (top_k full-size chunks) an operator can budget against.
        """
        on = self.knowledge is not None and self.knowledge_top_k > 0
        est_max = (
            _est_tokens_from_chars(self.knowledge_top_k * config.knowledge_chunk_chars)
            if on
            else 0
        )
        return {"auto_inject": on, "top_k": self.knowledge_top_k, "est_max_tokens": est_max}
