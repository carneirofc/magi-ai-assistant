"""Deliberate memory orchestration on top of `FileMemoryStore`.

Three memory kinds, each written *deliberately* — never auto-extracted by the
model framework:

  - short-term : the recent turns of the live session (rolling, capped window)
  - long-term  : durable facts the model chooses to keep about a user
  - episodic   : summaries of whole past interactions ("what happened")

Plus a global **persona** file: the personality + behavioral adjustments that
*evolve* as the model reflects on its interactions.

Scope (which user / session a write belongs to) flows through a `ContextVar` so
the model-facing tools don't need it threaded as an argument — the channel sets
the scope once per message, before the run, and every tool the model calls
during that run resolves the right files. `build_context` assembles the block
that gets injected into the run so the model actually *sees* its memory.
"""

from contextvars import ContextVar
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Awaitable, Callable, Optional

from agno.utils.log import log_info, log_warning

from core.config import config
from core.memory.semantic import MemoryRetriever, build_semantic_index
from core.memory.store import FileMemoryStore

# Rough provider-agnostic token estimate. We never see the real tokenizer through
# the proxy, so ~4 chars/token is the standard ballpark — good enough to monitor
# growth and warn before the window fills. Never used to truncate, only to alert.
_CHARS_PER_TOKEN = 4


def _est_tokens(text: str) -> int:
    return (len(text) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN


# An async summarizer: takes raw conversation turns, returns a one-line episode.
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
        summarize_fn: Optional[SummarizeFn] = None,
        summarize_every: int = 10,
        retriever: Optional[MemoryRetriever] = None,
        semantic_top_k: int = 5,
    ):
        self.store = store
        self.short_term_max = short_term_max
        # When set, turns evicted from the window are buffered and folded into an
        # episode every `summarize_every` turns instead of being lost (see
        # `maybe_summarize`). When None, eviction just drops — the prior behavior.
        self.summarize_fn = summarize_fn
        self.summarize_every = max(1, summarize_every)
        # When set, long-term/episodes are also embedded into a vector store and
        # `build_context(query=...)` retrieves only the top-k relevant entries
        # instead of injecting whole files. None => whole-file injection (default).
        self.retriever = retriever
        self.semantic_top_k = semantic_top_k
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

    # --- short-term turn recording (called by the channel, not the model) ---
    def record_user_turn(self, text: str) -> None:
        self._record_turn("user", text)

    def record_assistant_turn(self, text: str) -> None:
        self._record_turn("assistant", text)

    def _record_turn(self, role: str, text: str) -> None:
        s = self.scope()
        evicted = self.store.append_short_term(
            s.user_id, s.session_id, role, text, self.short_term_max
        )
        # Don't lose evicted turns: buffer them for summarization (no model call
        # here — that happens out-of-band in `maybe_summarize`). No-op when the
        # summarizer is disabled, preserving the plain "drop oldest" behavior.
        if evicted and self.summarize_fn is not None:
            size = self.store.append_pending(s.user_id, s.session_id, evicted)
            log_info(
                f"memory: buffered {len(evicted)} evicted turn(s) for summary "
                f"(pending={size}/{self.summarize_every}) user={s.user_id} session={s.session_id}"
            )

    async def maybe_summarize(self) -> Optional[str]:
        """Fold buffered evicted turns into one episode once enough have piled up.

        Channel awaits this after each turn. It's a no-op unless a summarizer is
        configured and the pending buffer has reached `summarize_every` turns, so
        model calls stay batched rather than one-per-turn.
        """
        if self.summarize_fn is None:
            return None
        s = self.scope()
        if self.store.count_pending(s.user_id, s.session_id) < self.summarize_every:
            return None
        pending = self.store.read_pending(s.user_id, s.session_id)
        if not pending.strip():
            return None
        try:
            summary = (await self.summarize_fn(pending)).strip()
        except Exception as exc:  # noqa: BLE001 — summarization must never break a chat.
            log_warning(f"memory: summarization failed, keeping buffer: {type(exc).__name__}: {exc}")
            return None
        if not summary:
            return None
        self.store.append_episode(s.user_id, summary)
        self._index(s.user_id, "episode", summary)
        self.store.clear_pending(s.user_id, s.session_id)
        log_info(f"memory: summarized evicted turns into episode for user {s.user_id}: {summary!r}")
        return summary

    # --- flush (called by the channel on a user command) --------------------
    def flush_session(self) -> int:
        """Clear the live short-term window for the current scope. Returns turns dropped.

        Long-term facts, episodes and persona are left intact — this only resets
        the running conversation context (the `!flush` command).
        """
        s = self.scope()
        dropped = self.store.clear_short_term(s.user_id, s.session_id)
        log_info(f"memory: flushed {dropped} short-term turn(s) for session {s.session_id}")
        return dropped

    # --- deliberate writes (the model calls these via tools) ----------------
    def remember(self, fact: str) -> str:
        s = self.scope()
        self.store.append_long_term(s.user_id, fact)
        self._index(s.user_id, "long_term", fact)
        log_info(f"memory: long-term written for user {s.user_id}: {fact!r}")
        return "Stored to long-term memory."

    def record_episode(self, summary: str) -> str:
        s = self.scope()
        self.store.append_episode(s.user_id, summary)
        self._index(s.user_id, "episode", summary)
        log_info(f"memory: episode written for user {s.user_id}: {summary!r}")
        return "Episode recorded."

    def _index(self, user_id: str, kind: str, text: str) -> None:
        """Mirror a deliberate write into the vector store (no-op when disabled)."""
        if self.retriever is not None:
            self.retriever.index(user_id, kind, text)

    def evolve_persona(self, adjustment: str) -> str:
        self.store.append_persona_note(adjustment)
        log_info(f"memory: persona evolved: {adjustment!r}")
        return "Persona adjustment recorded."

    # --- reads --------------------------------------------------------------
    def recall_long_term(self) -> str:
        return self.store.read_long_term(self.scope().user_id) or "(no long-term memory yet)"

    def recall_episodes(self, limit: int = 5) -> str:
        return (
            self.store.read_episodes(self.scope().user_id, limit)
            or "(no episodes recorded yet)"
        )

    # --- context assembly (injected into every run) -------------------------
    def _read_sections(self, query: str | None = None) -> dict[str, str]:
        """The four memory bodies for the current scope, by name (may be empty).

        With a retriever AND a `query`, long-term/episodes are the top-k entries
        most relevant to the query (so a long history isn't dumped wholesale);
        otherwise they're the whole files. Persona + short-term are always whole.
        """
        s = self.scope()
        long_term = self.store.read_long_term(s.user_id)
        episodes = self.store.read_episodes(s.user_id, self.short_term_max)
        if self.retriever is not None and query:
            lt_hits = self.retriever.search(s.user_id, query, "long_term", self.semantic_top_k)
            ep_hits = self.retriever.search(s.user_id, query, "episode", self.semantic_top_k)
            # Only override when the search actually returned something; on an empty
            # result (cold index / outage) keep the whole-file fallback.
            if lt_hits:
                long_term = "\n".join(f"- {h}" for h in lt_hits)
            if ep_hits:
                episodes = "\n".join(f"- {h}" for h in ep_hits)
            log_info(
                f"semantic: retrieved long_term={len(lt_hits)} episode={len(ep_hits)} "
                f"for query {query[:60]!r}"
            )
        return {
            "persona": self.store.read_persona(),
            "long_term": long_term,
            "episodes": episodes,
            "short_term": self.store.read_short_term(s.user_id, s.session_id),
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
            "short_term_turns": sum(
                1 for ln in parts["short_term"].splitlines() if ln.startswith("- ")
            ),
        }


@lru_cache(maxsize=1)
def get_memory() -> MemoryManager:
    """Process-wide singleton, wired from config. Shared by channel + tools."""
    root = Path(config.memory_dir)
    log_info(
        f"memory: FileMemoryStore at {root.resolve()}, "
        f"short_term_max={config.short_term_max}, "
        f"persona_seed={len(config.persona_seed)} chars"
    )
    store = FileMemoryStore(root)
    return MemoryManager(
        store=store,
        short_term_max=config.short_term_max,
        persona_seed=config.persona_seed,
        # `summarize_fn` is injected by the agent layer (it needs a model); core
        # stays model-free. Disabled until something attaches it.
        summarize_fn=None,
        summarize_every=config.summarize_every,
        retriever=build_semantic_index(),  # None unless SEMANTIC_MEMORY is on
        semantic_top_k=config.semantic_top_k,
    )
