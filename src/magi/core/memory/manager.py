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
the agent layer (see `magi/agent/summarizer.py`). Construction is done by `build_memory`
(magi/core/memory/__init__) — nothing is built inside `__init__`.
"""

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Optional

from agno.utils.log import log_info, log_warning

from magi.core.config import config
from magi.core.items import ItemArchive
from magi.core.memory.adapters import slug
from magi.core.memory.curation import CurateFn, CurationInput
from magi.core.memory.kinds import Episodes, LongTerm, Session, SummarizeFn
from magi.core.memory.semantic import MemoryRetriever
from magi.core.memory.store import FileMemoryStore, ScopedMemory

# Re-exported for the agent layer (magi/agent/summarizer.py, magi/agent/curator.py) and
# __init__; the canonical definitions live next to the code that consumes them.
__all__ = ["MemoryManager", "MemoryScope", "SummarizeFn", "CurateFn"]

# Rough provider-agnostic token estimate. We never see the real tokenizer through
# the proxy, so ~4 chars/token is the standard ballpark — good enough to monitor
# growth and warn before the window fills. Never used to truncate, only to alert.
_CHARS_PER_TOKEN = 4


def _est_tokens(text: str) -> int:
    return (len(text) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN


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
        summarize_every: int = 10,
        long_term_recent_raw: int = 5,
        retriever: Optional[MemoryRetriever] = None,
        semantic_top_k: int = 5,
        short_term_turn_max_chars: int = 4_000,
        session_pending_max: int = 30,
        session_summary_max_chars: int = 4_000,
        curate_fn: Optional[CurateFn] = None,
        long_term_fact_max_chars: int = 1_000,
        long_term_facts_max: int = 200,
        archive: Optional[ItemArchive] = None,
    ):
        self.store = store
        # The item archive (None = off). When set, the durable fact sheet is
        # snapshotted to the object store after each fact write, so a user's curated
        # profile has an off-disk source-of-truth copy alongside the JSON on disk and
        # the semantic mirror in Qdrant. See magi/core/items.
        self._archive = archive
        # Post-turn durable-memory curator (injected; None disables it). Owns the
        # long-term fact sheet when on — it revises it per-fact each turn (ADD/
        # UPDATE/DELETE) instead of the lead appending raw facts. See
        # magi/core/memory/curation.py + magi/agent/curator.py.
        self._curate_fn = curate_fn
        # When set, long-term/episodes are also embedded into a vector store so a kind
        # render can retrieve only the top-k relevant entries instead of the whole
        # file. None => whole-file injection (default). Owned by the kinds, not here.
        # The scoped kinds. Each owns its IO + render + write + (if foldable) fold
        # policy; this manager orchestrates them and handles the global persona.
        self.long_term = LongTerm(
            retriever, semantic_top_k, max(0, long_term_recent_raw),
            fact_max_chars=long_term_fact_max_chars,
            facts_max=long_term_facts_max,
        )
        self.episodes = Episodes(retriever, semantic_top_k, short_term_max)
        self.session = Session(
            short_term_max, summarize_session_fn, max(1, summarize_every),
            turn_max_chars=short_term_turn_max_chars,
            pending_max=session_pending_max,
            summary_max_chars=session_summary_max_chars,
        )
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
        self.session.record_turn(self.mem, role, text)

    async def maybe_summarize_session(self) -> Optional[str]:
        """Fold buffered evicted turns into the rolling session summary.

        Channel awaits this after each turn; no-op unless the session summarizer is
        set and the pending buffer has reached its threshold. (Delegates to `Session`.)
        """
        return await self.session.maybe_fold(self.mem)

    async def maybe_curate(self, user_message: str, assistant_reply: str) -> Optional[list[str]]:
        """Post-turn durable-memory pass: let the curator revise the profile, log an
        episode, or evolve the persona based on the turn just completed.

        Channel awaits this after each turn (like the summarizers). No-op unless a
        curator is configured. Any failure is swallowed — curation must never break a
        chat — and a malformed/empty result simply changes nothing. Returns the list
        of applied changes (subset of profile/episode/persona), or None.
        """
        if self._curate_fn is None:
            return None
        mem = self.mem
        inp = CurationInput(
            user_message=user_message,
            assistant_reply=assistant_reply,
            current_facts=self.long_term.render_for_curator(mem),
            persona=self.store.persona.read(),
        )
        try:
            result = await self._curate_fn(inp)
        except Exception as exc:  # noqa: BLE001 — curation must never break a chat.
            log_warning(f"memory: curation failed: {type(exc).__name__}: {exc}")
            return None

        applied: list[str] = []
        fact_ops = self.long_term.apply_ops(mem, result.operations)
        if fact_ops:
            applied.append("profile")
            log_info(f"memory: applied fact ops [{', '.join(fact_ops)}] for user {mem.user_id}")
            self._snapshot_facts(mem)
        if result.episode and result.episode.strip():
            self.episodes.record_episode(mem, result.episode.strip())
            applied.append("episode")
        if result.persona_adjustment and result.persona_adjustment.strip():
            self.store.persona.append(result.persona_adjustment.strip())
            applied.append("persona")
        if applied:
            log_info(f"memory: curated [{', '.join(applied)}] for user {mem.user_id}")
        return applied or None

    # --- flush / close (called by the channel) ------------------------------
    def flush_session(self) -> int:
        """Close the live session: carry its summary into a global episode, then wipe.

        Orchestrates the one cross-kind hand-off — `Session.close` returns the
        rolling summary (if any), which is recorded as an `Episodes` entry so the
        gist survives. Long-term, episodes and persona are otherwise untouched.
        Returns how many live turns were dropped (the `!flush` command).
        """
        mem = self.mem
        dropped, carried = self.session.close(mem)
        if carried:
            self.episodes.record_episode(mem, carried)
            log_info(f"memory: folded session summary into episode for user {mem.user_id}")
        log_info(f"memory: flushed {dropped} short-term turn(s) for session {mem.session_id}")
        return dropped

    # --- item archive snapshot ----------------------------------------------
    def _snapshot_facts(self, mem: ScopedMemory) -> None:
        """Archive the durable fact sheet as the item-archive original for this user.

        Bytes-only (the per-fact vectors live in the semantic mirror, so no doc
        vector here). No-op when the archive is off; never raises — a memory write
        must not break a chat."""
        if self._archive is None:
            return
        try:
            path = mem.long_term_facts.path
            data = path.read_bytes() if path.exists() else b"[]"
            self._archive.persist(
                "memory",
                slug(mem.user_id),
                data=data,
                content_type="application/json",
                metadata={"file": "long_term_facts.json", "user_id": mem.user_id},
            )
        except Exception as exc:  # noqa: BLE001 — archival must never break a chat.
            log_warning(f"memory: fact snapshot failed for {mem.user_id}: {type(exc).__name__}: {exc}")

    # --- deliberate writes (the model calls these via tools) ----------------
    def remember(self, fact: str) -> str:
        # Appends a raw bullet to long_term.md (the legacy raw log), NOT the curated
        # fact sheet — so this is deliberately not archived. The durable "items" are
        # the curator-owned facts in long_term_facts.json, snapshotted in maybe_curate.
        self.long_term.remember(self.mem, fact)
        log_info(f"memory: long-term written for user {self.scope().user_id}: {fact!r}")
        return "Stored to long-term memory."

    def record_episode(self, summary: str) -> str:
        self.episodes.record_episode(self.mem, summary)
        log_info(f"memory: episode written for user {self.scope().user_id}: {summary!r}")
        return "Episode recorded."

    def evolve_persona(self, adjustment: str) -> str:
        self.store.persona.append(adjustment)
        log_info(f"memory: persona evolved: {adjustment!r}")
        return "Persona adjustment recorded."

    # --- reads --------------------------------------------------------------
    def recall_long_term(self) -> str:
        # Render the curated profile (what the curator maintains), falling back to
        # raw facts when no profile has been written yet.
        return self.long_term.render(self.mem, None) or "(no long-term memory yet)"

    def recall_episodes(self, limit: int = 5) -> str:
        return self.episodes.recall(self.mem, limit) or "(no episodes recorded yet)"

    # --- context assembly (injected into every run) -------------------------
    def _read_sections(self, query: str | None = None) -> dict[str, str]:
        """The memory bodies for the current scope, by name (may be empty).

        Persona is global (rendered straight off the store); the scoped kinds render
        themselves against the current `mem` bundle.
        """
        mem = self.mem
        return {
            "persona": self.store.persona.read(),
            "long_term": self.long_term.render(mem, query),
            "episodes": self.episodes.render(mem, query),
            "short_term": self.session.render(mem, query),
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
            sections.append(f"{self.long_term.section_header}\n{parts['long_term']}")
        if parts["episodes"]:
            sections.append(f"{self.episodes.section_header}\n{parts['episodes']}")
        if parts["short_term"]:
            sections.append(f"{self.session.section_header}\n{parts['short_term']}")
        if self._curate_fn is not None:
            sections.append(
                "This memory persists across sessions and is kept current for you "
                "automatically after each turn — you never save anything yourself. "
                "Rely on it for continuity instead of asking the user to repeat "
                "themselves. Any deeper-recall tools you have are described in their "
                "own contracts."
            )
        else:
            sections.append(
                "This memory persists across sessions; nothing here is written "
                "automatically. Rely on it for continuity instead of asking the user "
                "to repeat themselves. Any deeper-recall tools you have are described "
                "in their own contracts."
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
