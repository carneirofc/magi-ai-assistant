"""Memory curation: the post-turn revision of durable memory, fact by fact.

Writing durable memory used to be the lead's job, decided inline mid-reply via
append-only `remember()` tools. That put memory reasoning on the latency-critical
path and could only ever *add* facts — never update or supersede them, so
contradictions piled up until a periodic fold happened to reconcile them.

The curator moves that decision off the lead onto a cheap post-turn pass: it
reads the just-finished turn against the current durable facts and persona and
returns the changes to apply. Each durable fact is id-addressable, so the curator
emits a small set of per-fact operations — ADD a new fact, UPDATE one that
changed, DELETE one that's now wrong, or NOOP (the empty list) — instead of
re-emitting the whole profile every turn. The latter grows unbounded with the
profile and risks the model silently dropping facts on rewrite; the per-fact model
touches only what the turn actually changed. `core/memory` stays model-free: the
actual model call is an injected `CurateFn` built in `agent/curator.py`, the same
seam the summarizers use. The manager applies the returned operations deterministically.
"""

from dataclasses import dataclass, field
from typing import Awaitable, Callable, Literal, Optional

# The per-fact verbs the curator may emit. NOOP isn't a `FactOp` — it's simply an
# empty `operations` list (the common case: the turn changed nothing durable).
FactOpKind = Literal["add", "update", "delete"]


@dataclass(frozen=True)
class FactOp:
    """One change to the durable fact sheet.

    - add    : `text` is a new fact (`fact_id` ignored).
    - update : replace the fact at `fact_id` with `text`.
    - delete : drop the fact at `fact_id` (`text` ignored).

    Operations targeting an unknown `fact_id` are silently skipped when applied —
    the curator works from a snapshot and must never break a chat.
    """

    op: FactOpKind
    fact_id: Optional[str] = None
    text: Optional[str] = None


@dataclass(frozen=True)
class CurationInput:
    """What the curator reads: this turn, plus the durable memory it may revise."""

    user_message: str
    assistant_reply: str
    current_facts: str  # the id-tagged durable facts for this user (may be empty)
    persona: str  # the global persona body (may be empty)


@dataclass(frozen=True)
class CurationResult:
    """What the curator decides. Every field is optional — the common case is all
    empty: the turn taught nothing durable."""

    # Per-fact changes to the durable profile; empty list means NOOP.
    operations: tuple[FactOp, ...] = field(default_factory=tuple)
    # A one-line episode to record at a natural close, or None.
    episode: Optional[str] = None
    # A general, lasting behavior rule to append to the persona, or None.
    persona_adjustment: Optional[str] = None

    @property
    def is_empty(self) -> bool:
        return not (self.operations or self.episode or self.persona_adjustment)


# An async curator: reads a turn + current durable memory, returns the changes.
# Injected by the agent layer (agent/curator.py) so `core` stays model-free.
CurateFn = Callable[[CurationInput], Awaitable[CurationResult]]
