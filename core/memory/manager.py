"""Deliberate memory orchestration on top of `FileMemoryStore`.

Memory kinds, each written *deliberately* — never auto-extracted by the model
framework:

  - short-term : the recent turns of the live session (rolling, capped window, JSON)
  - session    : a rolling LLM summary of turns that rolled out of the window
  - long-term  : durable facts the model chooses to keep about a user
  - long-term summary : an LLM-condensed profile of long-term, kept small
  - episodic   : summaries of whole past interactions ("what happened")

Plus a global **persona** file: the personality + behavioral adjustments that
*evolve* as the model reflects on its interactions.

Scope (which user / session a write belongs to) flows through a `ContextVar` so
the model-facing tools don't need it threaded as an argument — the channel sets
the scope once per message, before the run, and every tool the model calls during
that run resolves the right files. `build_context` assembles the block injected
into the run so the model actually *sees* its memory.

This layer is model-free: the two summarizers are injected as async callables by
the agent layer (see `agent/summarizer.py`). Construction is done by `build_memory`
(core/memory/__init__) — nothing is built inside `__init__`.
"""

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from agno.utils.log import log_info, log_warning

from core.config import config
from core.memory.semantic import MemoryRetriever
from core.memory.store import FileMemoryStore, ScopedMemory

# Rough provider-agnostic token estimate. We never see the real tokenizer through
# the proxy, so ~4 chars/token is the standard ballpark — good enough to monitor
# growth and warn before the window fills. Never used to truncate, only to alert.
_CHARS_PER_TOKEN = 4


def _est_tokens(text: str) -> int:
    return (len(text) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN


# An async summarizer: takes text, returns a compact summary.
SummarizeFn = Callable[[str], Awaitable[str]]


@dataclass(frozen=True)
class MemoryScope:
    """Who/where a memory write belongs to. Set per message, read by the tools."""

    user_id: str
    session_id: str


_scope: ContextVar[MemoryScope | None] = ContextVar("memory_scope", default=None)


class MemoryManager:
    """Scope-aware façade the channel and the model-facing tools share."""

    def __init__(
        self,
        store: FileMemoryStore,
        short_term_max: int,
        persona_seed: str = "",
        summarize_session_fn: Optional[SummarizeFn] = None,
        summarize_long_term_fn: Optional[SummarizeFn] = None,
        summarize_every: int = 10,
        long_term_summarize_every: int = 20,
        long_term_recent_raw: int = 5,
        retriever: Optional[MemoryRetriever] = None,
        semantic_top_k: int = 5,
    ):
        self.store = store
        self.short_term_max = short_term_max
        # When set, turns evicted from the window are buffered and folded into a
        # rolling session summary every `summarize_every` turns; on session close
        # that summary is recorded as a global episode. When None, eviction drops.
        self.summarize_session_fn = summarize_session_fn
        # When set, long-term facts are condensed into a profile once enough pile up.
        self.summarize_long_term_fn = summarize_long_term_fn
        self.summarize_every = max(1, summarize_every)
        self.long_term_summarize_every = max(1, long_term_summarize_every)
        self.long_term_recent_raw = max(0, long_term_recent_raw)
        # When set, long-term/episodes are also embedded into a vector store and
        # `build_context(query=...)` retrieves only the top-k relevant entries
        # instead of injecting whole files. None => whole-file injection (default).
        self.retriever = retriever
        self.semantic_top_k = semantic_top_k
        # Per-user long-term fact count at the last summary (in-memory; a restart
        # just re-summarizes once on the next threshold cross — harmless).
        self._lt_summarized_at: dict[str, int] = {}
        if persona_seed:
            self.store.seed_persona(persona_seed)

    # --- scope --------------------------------------------------------------
    def set_scope(self, user_id: object, session_id: object) -> MemoryScope:
        scope = MemoryScope(user_id=str(user_id), session_id=str(session_id))
        _scope.set(scope)
        return scope

    def scope(self) -> MemoryScope:
        scope = _scope.get()
        if scope is None:
            raise RuntimeError("memory scope not set; call set_scope() before use")
        return scope

    @property
    def mem(self) -> ScopedMemory:
        """The file adapters for the current scope.

        Resolved from the `ContextVar` on each access — never cached on the
        manager — so concurrent sessions sharing this single manager instance can't
        clobber one another's scope. Constructing the bundle is just path wrapping.
        """
        s = self.scope()
        return self.store.scoped(s.user_id, s.session_id)

    # --- short-term turn recording (called by the channel, not the model) ---
    def record_user_turn(self, text: str) -> None:
        self._record_turn("user", text)

    def record_assistant_turn(self, text: str) -> None:
        self._record_turn("assistant", text)

    def _record_turn(self, role: str, text: str) -> None:
        s = self.scope()
        evicted = self.mem.live_turns.append(role, text, self.short_term_max)
        # Don't lose evicted turns: buffer them for summarization (no model call
        # here — that happens in `maybe_summarize_session`). No-op when the session
        # summarizer is disabled, preserving the plain "drop oldest" behavior.
        if evicted and self.summarize_session_fn is not None:
            size = self.mem.pending.extend(evicted)
            log_info(
                f"memory: buffered {len(evicted)} evicted turn(s) for summary "
                f"(pending={size}/{self.summarize_every}) user={s.user_id} session={s.session_id}"
            )

    @staticmethod
    def _render_turns(turns: list[dict]) -> str:
        """Render JSON turns to the text the model sees (impl detail, not presentation)."""
        return "\n".join(f"- **{t.get('role', '?')}**: {t.get('content', '')}" for t in turns)

    async def _fold(
        self,
        *,
        fn: Optional[SummarizeFn],
        payload: Optional[str],
        write_back: Callable[[str], None],
        label: str,
    ) -> Optional[str]:
        """Guarded summarize: await `fn(payload)`, then persist via `write_back`.

        The one place the "summarization must never break a chat" contract lives:
        a no-op (returns None) when the summarizer is unset or there's nothing to
        fold (`payload` falsy), and any summarizer failure is logged and swallowed.
        The per-kind gate/source/write differences are passed in by the callers.
        """
        if fn is None or not payload:
            return None
        try:
            summary = (await fn(payload)).strip()
        except Exception as exc:  # noqa: BLE001 — summarization must never break a chat.
            log_warning(f"memory: {label} summary failed: {type(exc).__name__}: {exc}")
            return None
        if not summary:
            return None
        write_back(summary)
        log_info(f"memory: summarized {label}")
        return summary

    async def maybe_summarize_session(self) -> Optional[str]:
        """Fold buffered evicted turns into the rolling session summary.

        Channel awaits this after each turn. No-op unless a session summarizer is
        configured and the pending buffer has reached `summarize_every` turns.
        """
        if self.summarize_session_fn is None:
            return None
        s = self.scope()
        payload = None
        if self.mem.pending.count() >= self.summarize_every:
            pending = self.mem.pending.read()
            if pending:
                prior = self.mem.session_summary.read()
                payload = (
                    f"Prior summary:\n{prior or '(none)'}\n\n"
                    f"New turns:\n{self._render_turns(pending)}"
                )

        def write_back(summary: str) -> None:
            self.mem.session_summary.write(summary)
            self.mem.pending.delete()

        return await self._fold(
            fn=self.summarize_session_fn,
            payload=payload,
            write_back=write_back,
            label=f"session for user {s.user_id}",
        )

    async def maybe_summarize_long_term(self) -> Optional[str]:
        """Condense long-term facts into a profile once enough have accumulated.

        Channel awaits this after each turn. No-op unless a long-term summarizer is
        configured and at least `long_term_summarize_every` new facts have landed
        since the last summary.
        """
        if self.summarize_long_term_fn is None:
            return None
        s = self.scope()
        count = self.mem.long_term.count()
        last = self._lt_summarized_at.get(s.user_id, 0)
        payload = None
        if count >= self.long_term_summarize_every and count - last >= self.long_term_summarize_every:
            facts = self.mem.long_term.read()
            if facts.strip():
                payload = facts

        def write_back(summary: str) -> None:
            self.mem.long_term_summary.write(summary)
            self._lt_summarized_at[s.user_id] = count

        return await self._fold(
            fn=self.summarize_long_term_fn,
            payload=payload,
            write_back=write_back,
            label=f"long-term for user {s.user_id}",
        )

    # --- flush / close (called by the channel) ------------------------------
    def flush_session(self) -> int:
        """Close the live session: fold its summary into a global episode, then wipe.

        The session summary (if any) is recorded as an episode so the gist of the
        chat survives; long-term facts, episodes and persona are otherwise left
        intact. Returns how many live turns were dropped (the `!flush` command).
        """
        s = self.scope()
        summary = self.mem.session_summary.read()
        if summary.strip():
            episode = self._strip_header(summary)
            self.mem.episodes.append(episode)
            self._index(s.user_id, "episode", episode)
            log_info(f"memory: folded session summary into episode for user {s.user_id}")
        dropped = self.mem.live_turns.count()
        self.mem.live_turns.delete()
        self.mem.session_summary.delete()
        self.mem.pending.delete()
        log_info(f"memory: flushed {dropped} short-term turn(s) for session {s.session_id}")
        return dropped

    @staticmethod
    def _strip_header(blob: str) -> str:
        """Drop the leading markdown `# header` line from a summary blob."""
        lines = [ln for ln in blob.splitlines() if not ln.startswith("#")]
        return "\n".join(lines).strip()

    # --- deliberate writes (the model calls these via tools) ----------------
    def remember(self, fact: str) -> str:
        s = self.scope()
        self.mem.long_term.append(fact)
        self._index(s.user_id, "long_term", fact)
        log_info(f"memory: long-term written for user {s.user_id}: {fact!r}")
        return "Stored to long-term memory."

    def record_episode(self, summary: str) -> str:
        s = self.scope()
        self.mem.episodes.append(summary)
        self._index(s.user_id, "episode", summary)
        log_info(f"memory: episode written for user {s.user_id}: {summary!r}")
        return "Episode recorded."

    def _index(self, user_id: str, kind: str, text: str) -> None:
        """Mirror a deliberate write into the vector store (no-op when disabled)."""
        if self.retriever is not None:
            self.retriever.index(user_id, kind, text)

    def evolve_persona(self, adjustment: str) -> str:
        self.store.persona.append(adjustment)
        log_info(f"memory: persona evolved: {adjustment!r}")
        return "Persona adjustment recorded."

    # --- reads --------------------------------------------------------------
    def recall_long_term(self) -> str:
        return self.mem.long_term.read() or "(no long-term memory yet)"

    def recall_episodes(self, limit: int = 5) -> str:
        return self.mem.episodes.tail(limit) or "(no episodes recorded yet)"

    # --- context assembly (injected into every run) -------------------------
    def _long_term_section(self, query: str | None) -> str:
        """Long-term as: condensed summary + most-recent raw facts (or whole file).

        With a retriever AND a query, return the top-k entries most relevant to the
        query instead (so a long history isn't dumped wholesale).
        """
        if self.retriever is not None and query:
            hits = self.retriever.search(self.scope().user_id, query, "long_term", self.semantic_top_k)
            if hits:
                return "\n".join(f"- {h}" for h in hits)
        summary = self.mem.long_term_summary.read()
        if not summary:
            return self.mem.long_term.read()  # nothing condensed yet
        recent = self.mem.long_term.recent(self.long_term_recent_raw)
        parts = [self._strip_header(summary)]
        if recent:
            parts.append("Recent facts:\n" + "\n".join(f"- {r}" for r in recent))
        return "\n\n".join(parts)

    def _short_term_section(self) -> str:
        """Short-term as: rolling session summary (if any) + the live JSON turns."""
        summary = self.mem.session_summary.read()
        turns = self._render_turns(self.mem.live_turns.read())
        parts = []
        if summary:
            parts.append("Earlier this session:\n" + self._strip_header(summary))
        if turns:
            parts.append(turns)
        return "\n\n".join(parts)

    def _episodes_section(self, query: str | None) -> str:
        if self.retriever is not None and query:
            hits = self.retriever.search(self.scope().user_id, query, "episode", self.semantic_top_k)
            if hits:
                return "\n".join(f"- {h}" for h in hits)
        return self.mem.episodes.tail(self.short_term_max)

    def _read_sections(self, query: str | None = None) -> dict[str, str]:
        """The memory bodies for the current scope, by name (may be empty)."""
        return {
            "persona": self.store.persona.read(),
            "long_term": self._long_term_section(query),
            "episodes": self._episodes_section(query),
            "short_term": self._short_term_section(),
        }

    def build_context(self, query: str | None = None) -> str:
        """The memory block shown to the model this run. Empty sections omitted.

        Two scopes are made explicit so the model knows what carries over:
        **global** (persona + per-user facts + episodes — persist across every
        session) and **this session** (the recent turns of the current chat).
        """
        parts = self._read_sections(query)
        sections: list[str] = ["# Your memory (persistent — you decide what to keep)"]
        if parts["persona"]:
            sections.append(parts["persona"])
        if parts["long_term"]:
            sections.append(f"## What you remember about this user (global)\n{parts['long_term']}")
        if parts["episodes"]:
            sections.append(f"## Past episodes with this user (global)\n{parts['episodes']}")
        if parts["short_term"]:
            sections.append(f"## This session so far (short-term)\n{parts['short_term']}")
        sections.append(
            "You decide what persists — nothing is saved automatically:\n"
            "- `remember(fact)` — keep a durable fact about this user (global)\n"
            "- `record_episode(summary)` — log how an interaction went (global)\n"
            "- `evolve_persona(adjustment)` — change how you behave, for everyone"
        )
        context = "\n\n".join(sections)
        self._log_context_size(context, parts)
        return context

    def _log_context_size(self, context: str, parts: dict[str, str]) -> None:
        """Log the assembled size and warn when it nears the lead's window."""
        tokens = _est_tokens(context)
        budget = config.lead_num_ctx
        ratio = tokens / budget if budget else 0.0
        breakdown = ", ".join(f"{k}~{_est_tokens(v)}t" for k, v in parts.items())
        log_info(
            f"memory: context ~{tokens} tok ({ratio:.0%} of {budget}) [{breakdown}]"
        )
        if ratio >= config.ctx_warn_ratio:
            log_warning(
                f"memory: context ~{tokens} tok is {ratio:.0%} of the {budget}-tok window "
                f"(warn at {config.ctx_warn_ratio:.0%}) — consider !flush or trimming long-term"
            )

    def context_stats(self) -> dict:
        """Per-section + total size for the current scope (the `!ctx` command)."""
        parts = self._read_sections()
        context = self.build_context()
        tokens = _est_tokens(context)
        budget = config.lead_num_ctx
        return {
            "total_chars": len(context),
            "est_tokens": tokens,
            "budget_tokens": budget,
            "ratio": (tokens / budget) if budget else 0.0,
            "sections": {k: _est_tokens(v) for k, v in parts.items()},
            "short_term_turns": self.mem.live_turns.count(),
        }
