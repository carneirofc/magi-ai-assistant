"""Filesystem-backed memory store — deliberate, inspectable, no magic.

Durable memory kinds are plain markdown files; the live short-term window is JSON
so the raw conversation (role + content per turn) round-trips losslessly. Layout:

    <root>/
      persona.md                         # evolved behavior (base: prompts/team/lead.md)
      identity.json                      # global bot identity (name/description/avatar; magi/core/identity)
      identity/avatar.<ext>              # the bot's profile-picture bytes
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

from magi.core.identity import IdentityStore
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
        # The global bot identity (name, description, profile picture) — the
        # presented self, distinct from the persona's evolving behavior. Sits on
        # the root because it's non-scoped, like the persona. See magi/core/identity.
        self.identity = IdentityStore(self.root)

    def scoped(self, user_id: object, session_id: object) -> ScopedMemory:
        """The memory adapters for one (user, session) scope."""
        return ScopedMemory(self.root, user_id, session_id)

    # --- enumerate (admin) --------------------------------------------------
    def list_users(self) -> list[str]:
        """The user ids that have any memory on disk, sorted.

        These are the on-disk slugs (ids are slugged on write, see `slug`), which
        is the identity the admin tool addresses. Empty when nothing's been
        written yet. Used by the operator admin viewer (ADR 0002)."""
        users_dir = self.root / "users"
        if not users_dir.is_dir():
            return []
        return sorted(p.name for p in users_dir.iterdir() if p.is_dir())

    def list_sessions(self, user_id: object) -> list[str]:
        """The session ids with a live window on disk for `user_id`, sorted.

        Derived from the `<sid>.json` files under the user's sessions dir; the
        sidecar `<sid>.pending.json` and `<sid>.summary.md` are not sessions of
        their own and are excluded."""
        sessions_dir = self.root / "users" / slug(user_id) / "sessions"
        if not sessions_dir.is_dir():
            return []
        sids = [
            p.name[: -len(".json")]
            for p in sessions_dir.iterdir()
            if p.is_file() and p.name.endswith(".json") and not p.name.endswith(".pending.json")
        ]
        return sorted(sids)

    def seed_persona(self, text: str) -> None:
        """Write the base persona once, if no persona file exists yet."""
        self.persona.seed(
            f"# {_PERSONA_HEADER}\n\n{text.strip()}\n\n"
            "## Adjustments (evolve over time)\n\n"
        )
