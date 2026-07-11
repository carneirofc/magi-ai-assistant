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

import re
from pathlib import Path

from magi.core.identity import IdentityStore
from magi.core.memory.adapters import Blob, BulletLog, JsonFacts, JsonWindow, slug

_PERSONA_HEADER = "Persona & evolved behavior"


def _norm_bullet(text: str) -> str:
    """A comparison key for a persona adjustment: case/whitespace/trailing-punctuation
    insensitive, so trivially reworded restatements of the same rule collapse together."""
    return re.sub(r"\s+", " ", text).strip().lower().rstrip(".!?,;: ")


class ScopedMemory:
    """The six per-(user, session) memory files, each as its file-shape adapter."""

    def __init__(self, root: Path, user_id: object, session_id: object):
        self.user_id = str(user_id)
        self.session_id = str(session_id)
        users = root / "users" / slug(user_id)
        sessions = users / "sessions"
        sid = slug(session_id)
        self.long_term = BulletLog(
            users / "long_term.md", f"Long-term memory — user {user_id}",
            note_type="long-term", tags=["memory/long-term"],
        )
        # The curated profile: id-addressable facts the curator mutates per-fact.
        self.long_term_facts = JsonFacts(users / "long_term_facts.json")
        self.episodes = BulletLog(
            users / "episodic.md", f"Episodic memory — user {user_id}",
            note_type="episodic", tags=["memory/episodic"],
        )
        self.live_turns = JsonWindow(sessions / f"{sid}.json")
        self.session_summary = Blob(
            sessions / f"{sid}.summary.md", f"Session summary — session {session_id}",
            note_type="session-summary", tags=["memory/session"],
        )
        self.pending = JsonWindow(sessions / f"{sid}.pending.json")


class FileMemoryStore:
    """Root of the on-disk memory tree: the global persona + a per-scope bundle factory."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.persona = BulletLog(
            self.root / "persona.md", _PERSONA_HEADER,
            note_type="persona", tags=["memory/persona"],
        )
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

    def session_overview(self, user_id: object) -> list[dict]:
        """Metadata for every stored session of `user_id`, newest-activity first.

        One dict per session: `id`, `turns` (live-window count), `has_summary`,
        `last_ts` (the newest turn's timestamp, None for an empty window) and
        `preview` (the first user turn, trimmed). Pure IO for the session-archive
        endpoints; the client-side registry keeps its own titles."""
        out: list[dict] = []
        for sid in self.list_sessions(user_id):
            mem = self.scoped(user_id, sid)
            turns = mem.live_turns.read()
            preview = next(
                (str(t.get("content", "")) for t in turns if t.get("role") == "user"), ""
            )
            out.append(
                {
                    "id": sid,
                    "turns": len(turns),
                    "has_summary": bool(mem.session_summary.read().strip()),
                    "last_ts": turns[-1].get("ts") if turns else None,
                    "preview": preview[:160],
                }
            )
        out.sort(key=lambda s: s["last_ts"] or "", reverse=True)
        return out

    def search_history(self, user_id: object, query: str, limit: int = 8) -> list[dict]:
        """Case-insensitive substring search across a user's stored conversations:
        live transcripts, rolling session summaries, and episodes.

        Returns up to `limit` hit dicts — `kind` ("transcript" | "summary" |
        "episode"), `session_id` (None for episodes), `role`/`ts` (transcript
        hits only), and a `snippet` around the match. Plain file scan on purpose:
        the corpus is one user's history, and a scan needs no index to stay
        truthful. (Semantic recall, when enabled, rides the memory retriever —
        this is the always-available floor.)"""
        q = query.strip().lower()
        if not q or limit <= 0:
            return []

        def snippet(text: str, at: int, radius: int = 90) -> str:
            lo, hi = max(0, at - radius), min(len(text), at + len(q) + radius)
            piece = " ".join(text[lo:hi].split())
            prefix = "…" if lo > 0 else ""
            suffix = "…" if hi < len(text) else ""
            return f"{prefix}{piece}{suffix}"

        hits: list[dict] = []
        for sid in self.list_sessions(user_id):
            mem = self.scoped(user_id, sid)
            for turn in mem.live_turns.read():
                content = str(turn.get("content", ""))
                at = content.lower().find(q)
                if at >= 0:
                    hits.append(
                        {
                            "kind": "transcript",
                            "session_id": sid,
                            "role": turn.get("role"),
                            "ts": turn.get("ts"),
                            "snippet": snippet(content, at),
                        }
                    )
            summary = mem.session_summary.read()
            at = summary.lower().find(q)
            if at >= 0:
                hits.append(
                    {
                        "kind": "summary",
                        "session_id": sid,
                        "role": None,
                        "ts": None,
                        "snippet": snippet(summary, at),
                    }
                )
        # Episodes are per-user (the session id in the scope is irrelevant).
        episodes = self.scoped(user_id, "-").episodes
        for body in episodes.recent(0):
            at = body.lower().find(q)
            if at >= 0:
                hits.append(
                    {
                        "kind": "episode",
                        "session_id": None,
                        "role": None,
                        "ts": None,
                        "snippet": snippet(body, at),
                    }
                )
        # Newest transcript activity first; summaries/episodes (no ts) trail.
        hits.sort(key=lambda h: h["ts"] or "", reverse=True)
        return hits[:limit]

    def seed_persona(self, text: str) -> None:
        """Write the base persona once, if no persona file exists yet."""
        self.persona.seed(
            f"# {_PERSONA_HEADER}\n\n{text.strip()}\n\n"
            "## Adjustments (evolve over time)\n\n"
        )

    def compact_persona(self, max_adjustments: int = 0) -> int:
        """Dedupe (and optionally cap) the persona's evolving adjustment bullets in place.

        The curator appends one behavior rule per turn, and near-identical rules pile
        up — bloating every run's context with restatements of the same guidance. This
        collapses duplicate bullets (compared via `_norm_bullet`, keeping the first
        occurrence) and, when `max_adjustments > 0`, keeps only the newest that many.
        The prose base and headers are left untouched.

        Bullets are only touched within the '## Adjustments' section when that marker
        is present (the seed always writes one), so a `- ` list item in the prose base
        is never disturbed; a legacy persona without the marker is pure bullets, so all
        of them are deduped. No-op — and no write — when nothing changes. Returns the
        number of bullets dropped.
        """
        body = self.persona.read_clean()
        if not body:
            return 0
        lines = body.splitlines()
        start = next(
            (i + 1 for i, ln in enumerate(lines)
             if ln.lstrip().startswith("## ") and "adjustment" in ln.lower()),
            None,
        )
        if start is None:  # legacy file: no marker, no prose — dedupe from the first bullet
            start = next((i for i, ln in enumerate(lines) if ln.startswith("- ")), len(lines))
        head, region = lines[:start], lines[start:]

        region_bullets = [ln for ln in region if ln.startswith("- ")]
        seen: set[str] = set()
        deduped: list[str] = []
        for ln in region_bullets:
            key = _norm_bullet(ln[2:])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(ln)
        if max_adjustments > 0 and len(deduped) > max_adjustments:
            deduped = deduped[-max_adjustments:]  # keep the newest

        dropped = len(region_bullets) - len(deduped)
        if dropped <= 0:
            return 0
        new_body = "\n".join(head).rstrip() + "\n\n" + "\n".join(deduped) + "\n"
        self.persona.overwrite(new_body)
        return dropped
