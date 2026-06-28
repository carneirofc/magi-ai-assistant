"""Memory curation: the post-turn rewrite of durable memory.

Writing durable memory used to be the lead's job, decided inline mid-reply via
append-only `remember()` tools. That put memory reasoning on the latency-critical
path and could only ever *add* facts — never update or supersede them, so
contradictions piled up until a periodic fold happened to reconcile them.

The curator moves that decision off the lead onto a cheap post-turn pass: it
reads the just-finished turn against the current durable profile and persona and
returns what should change — rewriting the whole profile (so update/supersede is
free), optionally logging an episode or evolving the persona. `core/memory` stays
model-free: the actual model call is an injected `CurateFn` built in
`agent/curator.py`, the same seam the summarizers use. The manager applies the
returned result deterministically.
"""

from dataclasses import dataclass
from typing import Awaitable, Callable, Optional


@dataclass(frozen=True)
class CurationInput:
    """What the curator reads: this turn, plus the durable memory it may revise."""

    user_message: str
    assistant_reply: str
    current_profile: str  # the curated durable profile for this user (may be empty)
    persona: str  # the global persona body (may be empty)


@dataclass(frozen=True)
class CurationResult:
    """What the curator decides. Every field is optional — the common case is all
    None: the turn taught nothing durable."""

    # The COMPLETE rewritten durable profile, or None when nothing durable changed.
    profile: Optional[str] = None
    # A one-line episode to record at a natural close, or None.
    episode: Optional[str] = None
    # A general, lasting behavior rule to append to the persona, or None.
    persona_adjustment: Optional[str] = None

    @property
    def is_empty(self) -> bool:
        return not (self.profile or self.episode or self.persona_adjustment)


# An async curator: reads a turn + current durable memory, returns the changes.
# Injected by the agent layer (agent/curator.py) so `core` stays model-free.
CurateFn = Callable[[CurationInput], Awaitable[CurationResult]]
