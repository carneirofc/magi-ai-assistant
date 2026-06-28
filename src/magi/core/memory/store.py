"""Filesystem-backed memory store — deliberate, inspectable, no magic.

Durable memory kinds are plain markdown files; the live short-term window is JSON
so the raw conversation (role + content per turn) round-trips losslessly. Layout:

    <root>/
      persona.md                         # evolved behavior (base: prompts/team/lead.md)
      users/<user>/
        long_term.md                     # durable facts learned about the user
        long_term_facts.json             # curated profile: id-addressable facts (curator)
        episodic.md                      # summaries of past interactions (episodes)
        sessions/<session>.json          # short-term: recent turns (capped), JSON
        sessions/<session>.summary.md    # rolling summary of this session so far
        sessions/<session>.pending.json  # evicted turns awaiting session summary

This layer is pure IO: each file is one of four shapes (`BulletLog`, `Blob`,
`JsonWindow`, `JsonFacts` — see `adapters`), constructed with a resolved path. The global
persona lives on the store; per-(user, session) files come from `scoped()`, which
hands back a `ScopedMemory` bundle bound to that scope. No model calls, no scoping
policy, no context assembly here — `MemoryManager` layers those on top.
"""

from pathlib import Path

from magi.core.memory.adapters import Blob, BulletLog, JsonFacts, JsonWindow, slug

_PERSONA_HEADER = "Persona & evolved behavior"


class ScopedMemory:
    """The six per-(user, session) memory files, each as its file-shape adapter."""

    def __init__(self, root: Path, user_id: object, session_id: object):
        self.user_id = str(user_id)
        self.session_id = str(session_id)
        users = root / "users" / slug(user_id)
        sessions = users / "sessions"
        sid = slug(session_id)
        self.long_term = BulletLog(users / "long_term.md", f"Long-term memory — user {user_id}")
        # The curated profile: id-addressable facts the curator mutates per-fact.
        self.long_term_facts = JsonFacts(users / "long_term_facts.json")
        self.episodes = BulletLog(users / "episodic.md", f"Episodic memory — user {user_id}")
        self.live_turns = JsonWindow(sessions / f"{sid}.json")
        self.session_summary = Blob(
            sessions / f"{sid}.summary.md", f"Session summary — session {session_id}"
        )
        self.pending = JsonWindow(sessions / f"{sid}.pending.json")


class FileMemoryStore:
    """Root of the on-disk memory tree: the global persona + a per-scope bundle factory."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.persona = BulletLog(self.root / "persona.md", _PERSONA_HEADER)

    def scoped(self, user_id: object, session_id: object) -> ScopedMemory:
        """The memory adapters for one (user, session) scope."""
        return ScopedMemory(self.root, user_id, session_id)

    def seed_persona(self, text: str) -> None:
        """Write the base persona once, if no persona file exists yet."""
        self.persona.seed(
            f"# {_PERSONA_HEADER}\n\n{text.strip()}\n\n"
            "## Adjustments (evolve over time)\n\n"
        )
