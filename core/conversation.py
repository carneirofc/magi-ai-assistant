"""Channel-agnostic conversation orchestration.

Owns the run + memory flow for one inbound message, free of any channel concern
(no `discord` import, no formatting): scope the memory, assemble context, record
the turn, run the agent/team, record the reply, and fold summaries. Channels feed
plain inputs in and render the plain `ConversationReply` out.

`runner` is an agno `Agent` or `Team`; `memory` is a `MemoryManager`. Both are
injected — nothing is constructed here.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Optional, Protocol

from agno.utils.log import log_error, log_info, log_warning
from agno.utils.message import get_text_from_message

from core.memory import MemoryManager


class Runner(Protocol):
    """The slice of an agno `Agent`/`Team` this service drives: one run, awaited
    whole or consumed as an event stream (`stream=True` rides through kwargs)."""

    def arun(
        self, *, input: str, user_id: str, session_id: str, **kwargs: Any
    ) -> Any: ...

_ERROR_REPLY = "Sorry, there was an error processing your message. Please try again later."
# Run finished cleanly but the lead emitted nothing — e.g. a tool failed and it
# stalled instead of recovering. Never hand the channel silence: say so honestly.
_EMPTY_REPLY = (
    "I wasn't able to put together an answer for that — a step I tried didn't pan out. "
    "Mind rephrasing or asking again?"
)


@dataclass(frozen=True)
class ConversationReply:
    """The result of handling one message, in channel-neutral terms."""

    text: str
    reasoning: Optional[str] = None
    is_error: bool = False


@dataclass(frozen=True)
class ConversationDelta:
    """One streamed chunk of the reply text (see `handle_stream`)."""

    text: str


class ConversationService:
    def __init__(self, runner: Runner, memory: MemoryManager, channel_guidance: str = ""):
        self.runner = runner
        self.memory = memory
        # Channel-specific output rules (e.g. Discord markdown). Appended to the
        # run context so the base prompt stays channel-agnostic.
        self.channel_guidance = channel_guidance

    def _prepare_input(
        self, user_id: str, session_id: str, text: str, extra_context: str
    ) -> str:
        """Scope memory, record the inbound turn, and build this run's input.

        The context rides inside the run's input, never on the shared runner:
        mutating `runner.additional_context` races concurrent conversations (the
        team reads it mid-run, so one user could see another's memory).
        """
        self.memory.set_scope(user_id, session_id)
        # Assemble context in order: caller identity/channel info, persisted memory,
        # then channel output rules. The message is the retrieval query.
        parts = [extra_context, self.memory.build_context(query=text), self.channel_guidance]
        context = "\n\n".join(p for p in parts if p and p.strip())
        self.memory.record_user_turn(text)
        return f"<context>\n{context}\n</context>\n\n{text}" if context else text

    async def _finish_turn(
        self, reply: str, reasoning: Optional[str]
    ) -> ConversationReply:
        """Record the reply + fold memory; the one tail both run modes share."""
        if reply:
            self.memory.record_assistant_turn(reply)
            # Fold rolled-off turns + accumulated facts (no-ops unless enabled).
            await self.memory.maybe_summarize_session()
            await self.memory.maybe_summarize_long_term()
        elif not reasoning:
            # Completed with neither answer nor reasoning: the lead went silent
            # (commonly after a tool error). Return an honest fallback instead of
            # an empty string so the channel never has to invent one.
            log_warning("run completed with no content; returning fallback")
            return ConversationReply(text=_EMPTY_REPLY, is_error=True)
        return ConversationReply(text=reply, reasoning=reasoning, is_error=False)

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
        response = await self.runner.arun(
            input=run_input, user_id=user_id, session_id=session_id, **media
        )
        log_info(
            f"conversation: status={response.status}, "
            f"content_len={len(response.content or '')}"
        )

        if response.status == "ERROR":
            log_error(response.content)
            return ConversationReply(text=_ERROR_REPLY, is_error=True)

        reply = get_text_from_message(response.content) if response.content else ""
        return await self._finish_turn(reply, getattr(response, "reasoning_content", None))

    async def handle_stream(
        self,
        *,
        user_id: str | int,
        session_id: str,
        text: str,
        media: Optional[dict] = None,
        extra_context: str = "",
    ) -> AsyncIterator[ConversationDelta | ConversationReply]:
        """Like `handle`, but yields the reply incrementally.

        Yields a `ConversationDelta` per text chunk as the model produces it, then
        exactly one final `ConversationReply` (the authoritative result — channels
        should render it over the assembled deltas). Memory semantics are identical
        to `handle`: the turn is recorded and folded once, from the final text.
        """
        media = media or {}
        user_id = str(user_id)
        log_info(f"conversation: streaming (session={session_id}, user={user_id})")

        run_input = self._prepare_input(user_id, session_id, text, extra_context)
        chunks: list[str] = []
        final = None
        try:
            stream = self.runner.arun(
                input=run_input,
                user_id=user_id,
                session_id=session_id,
                stream=True,
                stream_events=False,
                # agno yields the full RunOutput as the stream's last item; that is
                # the same object the non-stream path gets, so both finish alike.
                yield_run_output=True,
                **media,
            )
            async for event in stream:
                if hasattr(event, "status"):  # the final RunOutput/TeamRunOutput
                    final = event
                    continue
                # Content deltas: `RunContent` (agent) / `TeamRunContent` (team).
                if getattr(event, "event", "").endswith("RunContent"):
                    delta = getattr(event, "content", None)
                    if isinstance(delta, str) and delta:
                        chunks.append(delta)
                        yield ConversationDelta(text=delta)
        except Exception as exc:  # noqa: BLE001 — the stream must end with a reply.
            log_error(f"conversation: stream failed: {type(exc).__name__}: {exc}")
            yield ConversationReply(text=_ERROR_REPLY, is_error=True)
            return

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
        yield await self._finish_turn(reply, reasoning)

    # --- control commands (channel formats the reply text) ------------------
    def flush(self, user_id: str | int, session_id: str) -> int:
        """Close the session (fold summary → episode, wipe live turns). Returns dropped."""
        self.memory.set_scope(user_id, session_id)
        return self.memory.flush_session()

    def context_stats(self, user_id: str | int, session_id: str) -> dict:
        self.memory.set_scope(user_id, session_id)
        return self.memory.context_stats()
