"""The scoped memory kinds, each one module behind a small interface.

A *kind* (long-term, episode, session) owns its own storage wiring, how it renders
into the model's context, how it's written, and — for the two that fold — its fold
policy (threshold + marker + payload). Persona is global and stays on the manager,
not here. See docs/adr/0001-per-kind-memory-modules.md.

Two protocols, composed (not a uniform base): every scoped kind `Renders`; only
long-term and session also `Fold`. Kinds are stateless across scopes — the manager
resolves the scope and passes the `ScopedMemory` bundle (which carries its own
`user_id` / `session_id`) into every call. The "must never break a chat" fold
guard and the retriever fallback each live once, as the helpers below.
"""

from typing import Awaitable, Callable, Iterable, Optional, Protocol, runtime_checkable

from agno.utils.log import log_info, log_warning

from core.memory.curation import FactOp
from core.memory.semantic import MemoryRetriever
from core.memory.store import ScopedMemory

# An async summarizer: takes text, returns a compact summary. Injected by the agent
# layer so `core` stays model-free.
SummarizeFn = Callable[[str], Awaitable[str]]


@runtime_checkable
class Renders(Protocol):
    """A kind that contributes a section to the assembled context block."""

    section_header: str

    def render(self, mem: ScopedMemory, query: Optional[str]) -> str: ...


@runtime_checkable
class Folds(Protocol):
    """A kind that compresses its overflow into a compact form on a threshold."""

    async def maybe_fold(self, mem: ScopedMemory) -> Optional[str]: ...


# --- shared helpers (each invariant lives here, once) -----------------------
def render_turns(turns: list[dict]) -> str:
    """Render JSON turns to the text the model sees (impl detail, not presentation)."""
    return "\n".join(f"- **{t.get('role', '?')}**: {t.get('content', '')}" for t in turns)


def strip_header(blob: str) -> str:
    """Drop the leading markdown `# header` line(s) from a summary blob."""
    lines = [ln for ln in blob.splitlines() if not ln.startswith("#")]
    return "\n".join(lines).strip()


def clamp(text: str, max_chars: int, label: str) -> str:
    """Hard cap on one memory payload (a turn, a summary). `max_chars <= 0`
    disables. The cut is marked so the model can see content was dropped.

    This is the size guardrail: the window caps *how many* turns are kept, this
    caps *how big* each piece may be — without it one pasted blob or a runaway
    summarizer output is replayed into every later run. The truncation marker
    counts against the budget, so the result never exceeds `max_chars`.
    """
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    cut = len(text) - max_chars
    log_warning(f"memory: {label} clamped to {max_chars} chars ({cut}+ dropped)")
    marker = f"\n…[truncated {cut}+ chars]"
    keep = max(0, max_chars - len(marker))
    return text[:keep] + marker


def index(retriever: Optional[MemoryRetriever], user_id: str, key: str, text: str) -> None:
    """Mirror a deliberate write into the vector store (no-op when disabled)."""
    if retriever is not None:
        retriever.index(user_id, key, text)


def retrieved_or(
    retriever: Optional[MemoryRetriever],
    user_id: str,
    query: Optional[str],
    key: str,
    top_k: int,
    fallback: Callable[[], str],
) -> str:
    """Top-k semantic hits for this kind when a retriever + query exist, else `fallback()`.

    The single home of the "search a long history instead of dumping it" branch,
    shared by every retrievable kind.
    """
    if retriever is not None and query:
        hits = retriever.search(user_id, query, key, top_k)
        if hits:
            return "\n".join(f"- {h}" for h in hits)
    return fallback()


async def guarded_fold(
    fn: Optional[SummarizeFn],
    payload: Optional[str],
    write_back: Callable[[str], None],
    label: str,
) -> Optional[str]:
    """Await `fn(payload)`, then persist via `write_back`. The one place the
    "summarization must never break a chat" contract lives: a no-op when the
    summarizer is unset or there's nothing to fold, and any failure is swallowed."""
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


# --- the kinds --------------------------------------------------------------
class LongTerm:
    """Durable per-user facts: a curated, id-addressable fact sheet (owned by the
    post-turn curator) plus any recent raw facts written via `remember`.

    The curator revises the fact sheet one fact at a time (ADD/UPDATE/DELETE),
    which is why this kind exposes both a context render (no ids, for the model's
    answer) and a curator render (id-tagged, so the next pass can target a fact)."""

    section_header = "## What you remember about this user (global)"
    retriever_key = "long_term"

    def __init__(
        self,
        retriever: Optional[MemoryRetriever],
        top_k: int,
        recent_raw: int,
        fact_max_chars: int = 1_000,
        facts_max: int = 200,
    ):
        self.retriever = retriever
        self.top_k = top_k
        self.recent_raw = recent_raw
        self.fact_max_chars = fact_max_chars
        self.facts_max = facts_max

    def render(self, mem: ScopedMemory, query: Optional[str]) -> str:
        return retrieved_or(
            self.retriever, mem.user_id, query, self.retriever_key, self.top_k,
            lambda: self._whole(mem),
        )

    def _whole(self, mem: ScopedMemory) -> str:
        facts = mem.long_term_facts.texts()
        if not facts:
            return mem.long_term.read()  # nothing curated yet; show raw facts
        parts = ["\n".join(f"- {f}" for f in facts)]
        recent = mem.long_term.recent(self.recent_raw)
        if recent:
            parts.append("Recent facts:\n" + "\n".join(f"- {r}" for r in recent))
        return "\n\n".join(parts)

    def render_for_curator(self, mem: ScopedMemory) -> str:
        """The durable facts the curator may revise, each tagged with its id so the
        next pass can UPDATE/DELETE it: `[id] text` lines. Empty when none yet."""
        return "\n".join(f"[{f['id']}] {f['text']}" for f in mem.long_term_facts.read())

    def apply_ops(self, mem: ScopedMemory, operations: Iterable[FactOp]) -> list[str]:
        """Apply the curator's per-fact operations to the fact sheet, in order.

        Each op is clamped (size guardrail) and mirrored into the retriever on
        add/update. UPDATE/DELETE against an unknown id is skipped (the curator
        worked from a snapshot). Returns the kinds actually applied (for logging)."""
        applied: list[str] = []
        for op in operations:
            if op.op == "add" and op.text and op.text.strip():
                text = clamp(op.text.strip(), self.fact_max_chars, "long-term fact")
                mem.long_term_facts.add(text)
                index(self.retriever, mem.user_id, self.retriever_key, text)
                applied.append("add")
            elif op.op == "update" and op.fact_id and op.text and op.text.strip():
                text = clamp(op.text.strip(), self.fact_max_chars, "long-term fact")
                if mem.long_term_facts.update(op.fact_id, text):
                    index(self.retriever, mem.user_id, self.retriever_key, text)
                    applied.append("update")
            elif op.op == "delete" and op.fact_id:
                if mem.long_term_facts.remove(op.fact_id):
                    applied.append("delete")
        dropped = mem.long_term_facts.trim(self.facts_max)
        if dropped:
            log_warning(
                f"memory: long-term facts over cap {self.facts_max}; dropped "
                f"{dropped} oldest for user {mem.user_id} — is the curator pruning?"
            )
        return applied

    def remember(self, mem: ScopedMemory, fact: str) -> None:
        mem.long_term.append(fact)
        index(self.retriever, mem.user_id, self.retriever_key, fact)

    def recall(self, mem: ScopedMemory) -> str:
        return mem.long_term.read()


class Episodes:
    """Summaries of whole past interactions. Written (by the model or by session
    close), rendered as a recent tail; never folds."""

    section_header = "## Past episodes with this user (global)"
    retriever_key = "episode"

    def __init__(self, retriever: Optional[MemoryRetriever], top_k: int, tail_limit: int):
        self.retriever = retriever
        self.top_k = top_k
        self.tail_limit = tail_limit

    def render(self, mem: ScopedMemory, query: Optional[str]) -> str:
        return retrieved_or(
            self.retriever, mem.user_id, query, self.retriever_key, self.top_k,
            lambda: mem.episodes.tail(self.tail_limit),
        )

    def record_episode(self, mem: ScopedMemory, summary: str) -> None:
        mem.episodes.append(summary)
        index(self.retriever, mem.user_id, self.retriever_key, summary)

    def recall(self, mem: ScopedMemory, limit: int) -> str:
        return mem.episodes.tail(limit)


class Session:
    """The live conversation: a capped window of recent turns + a rolling summary
    of turns evicted from it. Folds the pending buffer into that summary; on close
    hands the summary up to be recorded as an episode."""

    section_header = "## This session so far (short-term)"

    def __init__(
        self,
        short_term_max: int,
        summarize_fn: Optional[SummarizeFn],
        summarize_every: int,
        turn_max_chars: int = 4_000,
        pending_max: int = 30,
        summary_max_chars: int = 4_000,
    ):
        self.short_term_max = short_term_max
        self.summarize_fn = summarize_fn
        self.summarize_every = summarize_every
        self.turn_max_chars = turn_max_chars
        # The pending cap must clear the fold threshold, or the fold can never
        # trigger and the buffer silently churns oldest-out forever.
        self.pending_max = max(pending_max, summarize_every) if pending_max > 0 else 0
        self.summary_max_chars = summary_max_chars

    def render(self, mem: ScopedMemory, query: Optional[str] = None) -> str:
        summary = mem.session_summary.read()
        turns = render_turns(mem.live_turns.read())
        parts = []
        if summary:
            parts.append("Earlier this session:\n" + strip_header(summary))
        if turns:
            parts.append(turns)
        return "\n\n".join(parts)

    def record_turn(self, mem: ScopedMemory, role: str, text: str) -> None:
        text = clamp(text, self.turn_max_chars, f"{role} turn")
        evicted = mem.live_turns.append(role, text, self.short_term_max)
        # Don't lose evicted turns: buffer them for summarization (the model call is
        # in maybe_fold). No-op when the summarizer is disabled — plain drop-oldest.
        if evicted and self.summarize_fn is not None:
            before = mem.pending.count()
            size = mem.pending.extend(evicted, self.pending_max)
            dropped = before + len(evicted) - size
            if dropped > 0:
                # Pending only piles past its cap when folds keep failing (the
                # summarizer is down) — losing the oldest beats unbounded growth.
                log_warning(
                    f"memory: pending buffer at cap {self.pending_max}; dropped "
                    f"{dropped} oldest turn(s) — is the session summarizer failing? "
                    f"user={mem.user_id} session={mem.session_id}"
                )
            log_info(
                f"memory: buffered {len(evicted)} evicted turn(s) for summary "
                f"(pending={size}/{self.summarize_every}) user={mem.user_id} session={mem.session_id}"
            )

    async def maybe_fold(self, mem: ScopedMemory) -> Optional[str]:
        if self.summarize_fn is None:
            return None
        payload = None
        if mem.pending.count() >= self.summarize_every:
            pending = mem.pending.read()
            if pending:
                prior = mem.session_summary.read()
                payload = (
                    f"Prior summary:\n{prior or '(none)'}\n\n"
                    f"New turns:\n{render_turns(pending)}"
                )

        def write_back(summary: str) -> None:
            # A misbehaving summarizer (e.g. a thinking-trace leak) must not park
            # a giant blob that gets replayed into every later run.
            mem.session_summary.write(clamp(summary, self.summary_max_chars, "session summary"))
            mem.pending.delete()

        return await guarded_fold(
            self.summarize_fn, payload, write_back, f"session for user {mem.user_id}"
        )

    def close(self, mem: ScopedMemory) -> tuple[int, Optional[str]]:
        """Wipe the live window + summary + pending. Returns (turns dropped, the
        rolling summary body to carry forward as an episode, or None)."""
        summary = mem.session_summary.read()
        carried = strip_header(summary) if summary.strip() else None
        dropped = mem.live_turns.count()
        mem.live_turns.delete()
        mem.session_summary.delete()
        mem.pending.delete()
        return dropped, carried
