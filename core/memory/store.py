"""Filesystem-backed memory store — deliberate, inspectable, no magic.

Durable memory kinds are plain markdown files; the live short-term window is JSON
so the raw conversation (role + content per turn) round-trips losslessly. Layout:

    <root>/
      persona.md                         # evolved behavior (base: prompts/team/lead.md)
      users/<user>/
        long_term.md                     # durable facts learned about the user
        long_term_summary.md             # LLM-condensed profile of long_term.md
        episodic.md                      # summaries of past interactions (episodes)
        sessions/<session>.json          # short-term: recent turns (capped), JSON
        sessions/<session>.summary.md    # rolling summary of this session so far
        sessions/<session>.pending.json  # evicted turns awaiting session summary

This class does pure IO and nothing else — no model calls, no scoping policy, no
context assembly, no presentation formatting. `MemoryManager` layers scope +
assembly on top. Keeping the IO dumb is what makes the memory auditable: open the
file, read exactly what was stored.
"""

import json
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
    """Append-only markdown + JSON files, one per (kind, scope). Pure filesystem IO."""

    def __init__(self, root: Path):
        self.root = Path(root)

    # --- paths --------------------------------------------------------------
    def _persona_path(self) -> Path:
        return self.root / "persona.md"

    def _user_dir(self, user_id: object) -> Path:
        return self.root / "users" / _slug(user_id)

    def _long_term_path(self, user_id: object) -> Path:
        return self._user_dir(user_id) / "long_term.md"

    def _long_term_summary_path(self, user_id: object) -> Path:
        return self._user_dir(user_id) / "long_term_summary.md"

    def _episodic_path(self, user_id: object) -> Path:
        return self._user_dir(user_id) / "episodic.md"

    def _session_path(self, user_id: object, session_id: object) -> Path:
        return self._user_dir(user_id) / "sessions" / f"{_slug(session_id)}.json"

    def _session_summary_path(self, user_id: object, session_id: object) -> Path:
        return self._user_dir(user_id) / "sessions" / f"{_slug(session_id)}.summary.md"

    def _pending_path(self, user_id: object, session_id: object) -> Path:
        """Turns evicted from the window, buffered until summarized (see manager)."""
        return self._user_dir(user_id) / "sessions" / f"{_slug(session_id)}.pending.json"

    # --- primitive read/write ----------------------------------------------
    @staticmethod
    def _read(path: Path) -> str:
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8").strip()

    @staticmethod
    def _read_json(path: Path) -> list[dict]:
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError):
            return []

    @staticmethod
    def _write_json(path: Path, turns: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(turns, ensure_ascii=False, indent=2), encoding="utf-8")

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

    def _write_blob(self, path: Path, header: str, body: str) -> None:
        """Replace a whole single-blob file (header + body). Used for summaries."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {header}\n\n{body.strip()}\n", encoding="utf-8")

    @staticmethod
    def _bullets(text: str) -> list[str]:
        """The `- <ts> :: <content>` bullet bodies of a markdown file, in order."""
        out = []
        for ln in text.splitlines():
            if ln.startswith("- "):
                body = ln[2:]
                out.append(body.split(" :: ", 1)[1] if " :: " in body else body)
        return out

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

    def count_long_term(self, user_id: object) -> int:
        return len(self._bullets(self._read(self._long_term_path(user_id))))

    def recent_long_term(self, user_id: object, limit: int) -> list[str]:
        """The last `limit` long-term fact bodies (most recent at the end)."""
        bullets = self._bullets(self._read(self._long_term_path(user_id)))
        return bullets[-limit:] if limit > 0 else bullets

    def read_long_term_summary(self, user_id: object) -> str:
        return self._read(self._long_term_summary_path(user_id))

    def write_long_term_summary(self, user_id: object, summary: str) -> None:
        self._write_blob(
            self._long_term_summary_path(user_id),
            f"Long-term summary — user {user_id}",
            summary,
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

    # --- short-term window (per user+session, capped, JSON) -----------------
    def read_turns(self, user_id: object, session_id: object) -> list[dict]:
        """The live window as a list of `{"role","content","ts"}` dicts."""
        return self._read_json(self._session_path(user_id, session_id))

    def count_turns(self, user_id: object, session_id: object) -> int:
        return len(self.read_turns(user_id, session_id))

    def append_turn(
        self, user_id: object, session_id: object, role: str, content: str, max_entries: int
    ) -> list[dict]:
        """Append a turn, trim to the last `max_entries`, return the evicted turns.

        The returned dicts are the turns that just fell out of the window — the
        manager buffers them to summarize history before it's lost. Empty list
        when nothing was evicted.
        """
        path = self._session_path(user_id, session_id)
        turns = self.read_turns(user_id, session_id)
        turns.append({"role": role, "content": content, "ts": _now()})
        if len(turns) <= max_entries:
            self._write_json(path, turns)
            return []
        kept = turns[-max_entries:]
        evicted = turns[:-max_entries]
        self._write_json(path, kept)
        return evicted

    def clear_session(self, user_id: object, session_id: object) -> int:
        """Delete the session's window, summary and pending buffer. Returns turns dropped.

        This is the `!flush` / close path: wipes the live conversation without
        touching long-term/episodic.
        """
        dropped = self.count_turns(user_id, session_id)
        self._session_path(user_id, session_id).unlink(missing_ok=True)
        self._session_summary_path(user_id, session_id).unlink(missing_ok=True)
        self._pending_path(user_id, session_id).unlink(missing_ok=True)
        return dropped

    # --- rolling session summary (per user+session) -------------------------
    def read_session_summary(self, user_id: object, session_id: object) -> str:
        return self._read(self._session_summary_path(user_id, session_id))

    def write_session_summary(self, user_id: object, session_id: object, summary: str) -> None:
        self._write_blob(
            self._session_summary_path(user_id, session_id),
            f"Session summary — session {session_id}",
            summary,
        )

    # --- pending-summary buffer (per user+session, JSON) --------------------
    def append_pending(
        self, user_id: object, session_id: object, turns: list[dict]
    ) -> int:
        """Stash evicted turns to summarize later. Returns the buffer's new size."""
        path = self._pending_path(user_id, session_id)
        buffered = self._read_json(path)
        buffered.extend(turns)
        self._write_json(path, buffered)
        return len(buffered)

    def read_pending(self, user_id: object, session_id: object) -> list[dict]:
        return self._read_json(self._pending_path(user_id, session_id))

    def count_pending(self, user_id: object, session_id: object) -> int:
        return len(self.read_pending(user_id, session_id))

    def clear_pending(self, user_id: object, session_id: object) -> None:
        self._pending_path(user_id, session_id).unlink(missing_ok=True)
