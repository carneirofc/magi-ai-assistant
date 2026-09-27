"""Full-text search over past conversations (SQLite FTS5).

Memory keeps what the curator decided is durable; this index keeps what was
actually *said*, so the assistant can look back ("what did we decide about
the backup script last week?"). Every finished turn is appended — the user's
message and the reply — under its memory scope, and `search` only ever reads
one user's rows. It is its own SQLite file (`session_index_path`), separate
from agno's session db, and costs nothing when unused.

Pure IO, model-free. When the local SQLite lacks FTS5, `open_session_index`
returns None and the feature degrades to "no tool".
"""

import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from magi.core.log import log_info, log_warning

type Role = Literal["user", "assistant"]

_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS turns USING fts5(
    text,
    user_id UNINDEXED,
    session_id UNINDEXED,
    role UNINDEXED,
    ts UNINDEXED,
    tokenize = 'unicode61 remove_diacritics 2'
)
"""
_WORD_RE = re.compile(r"\w+", re.UNICODE)
_MAX_TEXT = 8_000


class TurnIndexer(Protocol):
    """What `ConversationService` needs: append one line of a finished turn."""

    def add(self, *, user_id: str, session_id: str, role: Role, text: str) -> None: ...


@dataclass(frozen=True)
class SessionHit:
    session_id: str
    role: str
    ts: str
    snippet: str


def fts5_available() -> bool:
    try:
        with sqlite3.connect(":memory:") as conn:
            conn.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
    except sqlite3.OperationalError:
        return False
    return True


def match_expression(query: str) -> str | None:
    """User text → a safe FTS5 query: each word quoted (no operator injection),
    OR-ed so partial matches still rank (bm25 orders them)."""
    words = _WORD_RE.findall(query.lower())[:16]
    return " OR ".join(f'"{w}"' for w in words) if words else None


class SessionIndex:
    """One FTS5 table in its own file; thread-safe for the gateway's workers."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock, self._conn:
            self._conn.execute(_SCHEMA)

    def add(self, *, user_id: str, session_id: str, role: Role, text: str) -> None:
        text = text.strip()
        if not text:
            return
        ts = datetime.now(UTC).isoformat(timespec="seconds")
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO turns (text, user_id, session_id, role, ts) VALUES (?, ?, ?, ?, ?)",
                (text[:_MAX_TEXT], user_id, session_id, role, ts),
            )

    def search(self, *, user_id: str, query: str, limit: int = 8) -> list[SessionHit]:
        """This user's best-matching lines, most relevant first."""
        expr = match_expression(query)
        if expr is None:
            return []
        with self._lock:
            rows: list[tuple[str, str, str, str]] = self._conn.execute(
                "SELECT session_id, role, ts, snippet(turns, 0, '[', ']', '…', 24) "
                "FROM turns WHERE turns MATCH ? AND user_id = ? ORDER BY rank LIMIT ?",
                (expr, user_id, max(1, min(limit, 25))),
            ).fetchall()
        return [SessionHit(session_id=s, role=r, ts=t, snippet=n) for s, r, t, n in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def open_session_index(path: Path) -> SessionIndex | None:
    """The index at `path`, or None (with a warning) when FTS5 is missing or
    the file can't be opened — session search then stays off."""
    if not fts5_available():
        log_warning("session search: this SQLite has no FTS5 — feature off")
        return None
    try:
        index = SessionIndex(path)
    except sqlite3.Error as exc:
        log_warning(f"session search: cannot open {path}: {exc}")
        return None
    log_info(f"session search: ENABLED ({path})")
    return index
