"""Filesystem-backed memory store — deliberate, inspectable, no magic.

Every memory kind is a plain markdown file under `root`, append-only with ISO
timestamps, so the whole of the model's memory is greppable and hand-editable.
Layout:

    <root>/
      persona.md                       # evolved behavior (base persona: prompts/team/lead.md)
      users/<user>/
        long_term.md                   # durable facts learned about the user
        episodic.md                    # summaries of past interactions (episodes)
        sessions/<session>.md          # short-term: recent turns (capped)

This class does pure IO and nothing else — no model calls, no scoping policy, no
context assembly. `MemoryManager` layers scope + assembly on top. Keeping the IO
dumb is what makes the memory auditable: open the file, read exactly what the
model will be told.
"""

import re
from datetime import datetime
from pathlib import Path


def _slug(value: object) -> str:
    """Filesystem-safe token for a user/session id (ids are ints or strings)."""
    text = str(value).strip()
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", text)
    return cleaned or "unknown"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class FileMemoryStore:
    """Append-only markdown files, one per (kind, scope). Pure filesystem IO."""

    def __init__(self, root: Path):
        self.root = Path(root)

    # --- paths --------------------------------------------------------------
    def _persona_path(self) -> Path:
        return self.root / "persona.md"

    def _user_dir(self, user_id: object) -> Path:
        return self.root / "users" / _slug(user_id)

    def _long_term_path(self, user_id: object) -> Path:
        return self._user_dir(user_id) / "long_term.md"

    def _episodic_path(self, user_id: object) -> Path:
        return self._user_dir(user_id) / "episodic.md"

    def _session_path(self, user_id: object, session_id: object) -> Path:
        return self._user_dir(user_id) / "sessions" / f"{_slug(session_id)}.md"

    def _pending_path(self, user_id: object, session_id: object) -> Path:
        """Turns evicted from the window, buffered until summarized (see manager)."""
        return self._user_dir(user_id) / "sessions" / f"{_slug(session_id)}.pending.md"

    # --- primitive read/write ----------------------------------------------
    @staticmethod
    def _read(path: Path) -> str:
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8").strip()

    @staticmethod
    def _ensure_header(path: Path, header: str) -> None:
        """Create the file with a markdown header the first time it's written."""
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"# {header}\n\n", encoding="utf-8")

    def _append_entry(self, path: Path, header: str, content: str) -> None:
        """Append one timestamped bullet (`- <ts> :: <content>`)."""
        self._ensure_header(path, header)
        line = f"- {_now()} :: {content.strip()}\n"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)

    # --- persona (global) ---------------------------------------------------
    def read_persona(self) -> str:
        return self._read(self._persona_path())

    def append_persona_note(self, note: str) -> None:
        self._append_entry(self._persona_path(), "Persona & evolved behavior", note)

    def seed_persona(self, text: str) -> None:
        """Write the base persona once, if no persona file exists yet."""
        path = self._persona_path()
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"# Persona & evolved behavior\n\n{text.strip()}\n\n"
            "## Adjustments (evolve over time)\n\n",
            encoding="utf-8",
        )

    # --- long-term (per user) ----------------------------------------------
    def read_long_term(self, user_id: object) -> str:
        return self._read(self._long_term_path(user_id))

    def append_long_term(self, user_id: object, fact: str) -> None:
        self._append_entry(
            self._long_term_path(user_id), f"Long-term memory — user {user_id}", fact
        )

    # --- episodic (per user) ------------------------------------------------
    def read_episodes(self, user_id: object, limit: int | None = None) -> str:
        text = self._read(self._episodic_path(user_id))
        if limit is None or not text:
            return text
        lines = text.splitlines()
        header = [ln for ln in lines if ln.startswith("#")]
        bullets = [ln for ln in lines if ln.startswith("- ")]
        kept = bullets[-limit:]
        return "\n".join(header + ([""] if header else []) + kept).strip()

    def append_episode(self, user_id: object, summary: str) -> None:
        self._append_entry(
            self._episodic_path(user_id), f"Episodic memory — user {user_id}", summary
        )

    # --- short-term (per user+session, capped) ------------------------------
    def read_short_term(self, user_id: object, session_id: object) -> str:
        return self._read(self._session_path(user_id, session_id))

    def append_short_term(
        self, user_id: object, session_id: object, role: str, text: str, max_entries: int
    ) -> list[str]:
        """Append a turn, trim to the last `max_entries`, return the evicted bullets.

        The returned bullets are the turns that just fell out of the window — the
        manager uses them to summarize history before it's lost. Empty list when
        nothing was evicted.
        """
        path = self._session_path(user_id, session_id)
        self._append_entry(path, f"Short-term — session {session_id}", f"**{role}**: {text}")
        return self._trim(path, max_entries)

    def clear_short_term(self, user_id: object, session_id: object) -> int:
        """Delete the session's short-term window. Returns how many turns were dropped.

        This is the `!flush` path: wipes the live conversation window (and any
        not-yet-summarized pending buffer) without touching long-term/episodic.
        """
        path = self._session_path(user_id, session_id)
        bullets = [ln for ln in self._read(path).splitlines() if ln.startswith("- ")]
        path.unlink(missing_ok=True)
        self._pending_path(user_id, session_id).unlink(missing_ok=True)
        return len(bullets)

    @staticmethod
    def _trim(path: Path, max_entries: int) -> list[str]:
        lines = path.read_text(encoding="utf-8").splitlines()
        header = [ln for ln in lines if ln.startswith("#")]
        bullets = [ln for ln in lines if ln.startswith("- ")]
        if len(bullets) <= max_entries:
            return []
        kept = bullets[-max_entries:]
        evicted = bullets[:-max_entries]
        path.write_text("\n".join(header + [""] + kept) + "\n", encoding="utf-8")
        return evicted

    # --- pending-summary buffer (per user+session) --------------------------
    def append_pending(self, user_id: object, session_id: object, bullets: list[str]) -> int:
        """Stash evicted turns to summarize later. Returns the buffer's new size."""
        path = self._pending_path(user_id, session_id)
        self._ensure_header(path, f"Pending summary — session {session_id}")
        with path.open("a", encoding="utf-8") as fh:
            for bullet in bullets:
                fh.write(bullet.rstrip("\n") + "\n")
        return self.count_pending(user_id, session_id)

    def read_pending(self, user_id: object, session_id: object) -> str:
        return self._read(self._pending_path(user_id, session_id))

    def count_pending(self, user_id: object, session_id: object) -> int:
        text = self._read(self._pending_path(user_id, session_id))
        return sum(1 for ln in text.splitlines() if ln.startswith("- "))

    def clear_pending(self, user_id: object, session_id: object) -> None:
        self._pending_path(user_id, session_id).unlink(missing_ok=True)
