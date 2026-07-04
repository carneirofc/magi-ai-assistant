"""Four file-shape adapters the memory store is built from.

Every memory file is one of these shapes; each adapter is constructed with a path
and owns the on-disk format for that shape. Nothing here knows about *kinds*
(long-term vs episodes vs ...) or scope — that lives one layer up in the store's
scope-bound bundle. Keeping the IO dumb is what makes memory auditable.

  - `BulletLog`   — append-only `- <content>` markdown bullets (logs)
  - `Blob`        — single header + body, whole-file replace (summaries)
  - `JsonWindow`  — a JSON list of turn dicts (live window + pending buffer)
  - `JsonFacts`   — a JSON list of id-addressable facts (the curated profile)

The two markdown shapes (`BulletLog`, `Blob`) are Obsidian-native notes: each file
opens in an Obsidian vault with a YAML frontmatter block (`type`, `tags`, `created`)
that Obsidian surfaces as note properties. Frontmatter is metadata, not note content,
so every read returns the body with the frontmatter stripped — it never reaches the
model context or the operator viewer, only Obsidian. Bullets are untimestamped (the
`created` stamp lives in frontmatter); the legacy `- <ts> :: <content>` line is still
parsed on read so files written before this format round-trip unchanged.
"""

import json
import re
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from agno.utils.log import log_warning


def slug(value: object) -> str:
    """Filesystem-safe token for a user/session id (ids are ints or strings)."""
    text = str(value).strip()
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", text)
    return cleaned or "unknown"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# --- write notifications ----------------------------------------------------
# The IO layer stays dumb, but it announces *that* a file changed so an optional
# backend can react — the git-backed memory (magi/core/memory/git_backend) uses this
# to commit each write. Exactly one observer at a time (one repo per memory root, one
# process); None keeps writes plain. The identity store emits through here too, so
# the whole memory tree — not just these adapters — is versioned. Kept here, in the
# leaf IO module, so both the adapters and the identity store can import it without a
# cycle (the git backend depends on this, never the reverse).
WriteObserver = Callable[[Path], None]
_write_observer: WriteObserver | None = None


def set_write_observer(observer: WriteObserver | None) -> None:
    """Register (or clear, with None) the sink notified after every file mutation."""
    global _write_observer
    _write_observer = observer


def emit_write(path: Path) -> None:
    """Notify the write observer that `path` was just written/removed (no-op when
    none is registered). A failing observer must never break the memory write that
    triggered it, so its errors are swallowed with a warning."""
    observer = _write_observer
    if observer is None:
        return
    try:
        observer(path)
    except Exception as exc:  # noqa: BLE001 — a write observer must never break a memory write.
        log_warning(
            f"memory: write observer failed for {path.name}: {type(exc).__name__}: {exc}"
        )


# --- Obsidian frontmatter ---------------------------------------------------
_FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)


def _frontmatter(note_type: str, tags: list[str]) -> str:
    """A leading Obsidian YAML frontmatter block: type + tags + a created stamp.

    Tags use inline (flow) `[a, b]` style deliberately: a block list would emit
    `- tag` lines, which the bullet parsers below would mistake for content.
    """
    return (
        "---\n"
        f"type: {note_type}\n"
        f"tags: [{', '.join(tags)}]\n"
        f"created: {_now()}\n"
        "---\n"
    )


def strip_frontmatter(text: str) -> str:
    """Drop a leading Obsidian frontmatter block, if present (else return as-is).

    Frontmatter is Obsidian metadata, not note content — the markdown adapters
    return the body without it so it never reaches the model context or the
    operator viewer. Files written before frontmatter existed pass through untouched.
    """
    return _FRONTMATTER_RE.sub("", text, count=1)


# Retired per-line bullet timestamp: `- 2026-07-03T22:09:48 :: <content>`. The
# `created` stamp now lives in frontmatter, so bullets are written untimestamped —
# but files in the old format still carry it, and a whole-note `read()` returns it
# verbatim. Anchored to a leading ISO stamp so a `::` inside real content is never hit.
_LEGACY_TS_BULLET = re.compile(r"^(- )\d{4}-\d{2}-\d{2}T[\d:]+ :: ")


def strip_legacy_ts(text: str) -> str:
    """Normalize legacy `- <ts> :: <content>` bullets to `- <content>`.

    Prose, headers, and already-clean bullets pass through untouched. Use where a
    note body is injected verbatim into model context (the persona): `read()` keeps
    the legacy timestamps, which are noise to the model. `bodies()` already drops
    them when it splits a log into content; this does the same at the line level
    while preserving the non-bullet lines a whole-note read must keep.
    """
    return "\n".join(_LEGACY_TS_BULLET.sub(r"\1", ln) for ln in text.splitlines())


class BulletLog:
    """Append-only Obsidian note: frontmatter, a `# header`, then `- <content>` bullets."""

    def __init__(self, path: Path, header: str, note_type: str = "note", tags: list[str] | None = None):
        self.path = Path(path)
        self.header = header
        self.note_type = note_type
        self.tags = tags or []

    def _ensure_header(self) -> None:
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            head = _frontmatter(self.note_type, self.tags) + f"# {self.header}\n\n"
            self.path.write_text(head, encoding="utf-8")

    def append(self, content: str) -> None:
        """Append one bullet (untimestamped — the frontmatter `created` stamps the file)."""
        self._ensure_header()
        line = f"- {content.strip()}\n"
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line)
        emit_write(self.path)

    def read(self) -> str:
        """The note body — frontmatter stripped, whitespace-trimmed (empty when absent)."""
        if not self.path.exists():
            return ""
        return strip_frontmatter(self.path.read_text(encoding="utf-8")).strip()

    def read_clean(self) -> str:
        """`read()` with retired per-line bullet timestamps normalized away.

        For consumers that inject the whole note body as-is (the persona), so legacy
        `- <ts> :: ` bullets don't leak their timestamps into context. Clean files are
        returned unchanged; prose and headers are preserved.
        """
        return strip_legacy_ts(self.read())

    def overwrite(self, body: str) -> None:
        """Replace the whole note body (frontmatter preserved, else freshly stamped).

        The append-only counterpart is `append`; this is for deliberate compaction
        (e.g. deduping the persona's evolving adjustments), not the hot write path.
        A file without frontmatter gains one — bringing a legacy note up to format.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existing = self.path.read_text(encoding="utf-8") if self.path.exists() else ""
        match = _FRONTMATTER_RE.match(existing)
        front = match.group(0) if match else _frontmatter(self.note_type, self.tags)
        self.path.write_text(front + body.strip() + "\n", encoding="utf-8")
        emit_write(self.path)

    def bodies(self) -> list[str]:
        """The bullet bodies, in order. Handles both the current `- <content>` form
        and the legacy `- <ts> :: <content>` form (content after `:: `)."""
        out = []
        for ln in self.read().splitlines():
            if ln.startswith("- "):
                body = ln[2:]
                out.append(body.split(" :: ", 1)[1] if " :: " in body else body)
        return out

    def count(self) -> int:
        return len(self.bodies())

    def recent(self, limit: int) -> list[str]:
        """The last `limit` bodies (all of them when limit <= 0)."""
        bodies = self.bodies()
        return bodies[-limit:] if limit > 0 else bodies

    def tail(self, limit: int | None = None) -> str:
        """The header plus the last `limit` raw bullet *lines* (timestamps kept).

        Used where the rendered tail is injected verbatim (episodes). `limit=None`
        returns the whole file.
        """
        text = self.read()
        if limit is None or not text:
            return text
        lines = text.splitlines()
        header = [ln for ln in lines if ln.startswith("#")]
        kept = [ln for ln in lines if ln.startswith("- ")][-limit:]
        return "\n".join(header + ([""] if header else []) + kept).strip()

    def seed(self, scaffold: str) -> None:
        """Write a one-time initial body (frontmatter + scaffold) if the file is absent.

        The scaffold carries its own `# header`, so frontmatter is the only prefix added.
        """
        if self.path.exists():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(_frontmatter(self.note_type, self.tags) + scaffold, encoding="utf-8")
        emit_write(self.path)

    def delete(self) -> None:
        self.path.unlink(missing_ok=True)
        emit_write(self.path)


class Blob:
    """A single Obsidian note — frontmatter, a `# header`, and a body — replaced whole.

    Used for summaries. Like `BulletLog`, `read` returns the body with the frontmatter
    stripped, so the metadata reaches Obsidian but not the model context.
    """

    def __init__(self, path: Path, header: str, note_type: str = "note", tags: list[str] | None = None):
        self.path = Path(path)
        self.header = header
        self.note_type = note_type
        self.tags = tags or []

    def read(self) -> str:
        if not self.path.exists():
            return ""
        return strip_frontmatter(self.path.read_text(encoding="utf-8")).strip()

    def write(self, body: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        head = _frontmatter(self.note_type, self.tags)
        self.path.write_text(f"{head}# {self.header}\n\n{body.strip()}\n", encoding="utf-8")
        emit_write(self.path)

    def delete(self) -> None:
        self.path.unlink(missing_ok=True)
        emit_write(self.path)


class JsonWindow:
    """A JSON list of turn dicts. Two writers: a capped window and a buffer."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def read(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            # A corrupt/unreadable window must not break a chat — but silently
            # dropping the whole turn history would hide real data loss, so warn.
            log_warning(
                f"memory: unreadable JSON window {self.path.name}, dropping it "
                f"({type(exc).__name__}: {exc})"
            )
            return []
        return data if isinstance(data, list) else []

    def count(self) -> int:
        return len(self.read())

    def _write(self, turns: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(turns, ensure_ascii=False, indent=2), encoding="utf-8")
        emit_write(self.path)

    def append(self, role: str, content: str, max_entries: int) -> list[dict]:
        """Append a turn, trim to the last `max_entries`, return the evicted turns."""
        turns = self.read()
        turns.append({"role": role, "content": content, "ts": _now()})
        if len(turns) <= max_entries:
            self._write(turns)
            return []
        kept = turns[-max_entries:]
        evicted = turns[:-max_entries]
        self._write(kept)
        return evicted

    def extend(self, turns: list[dict], max_entries: int = 0) -> int:
        """Append turn dicts; when `max_entries` > 0 keep only the newest that many.
        Returns the buffer's new size."""
        buffered = self.read()
        buffered.extend(turns)
        if max_entries > 0 and len(buffered) > max_entries:
            buffered = buffered[-max_entries:]
        self._write(buffered)
        return len(buffered)

    def delete(self) -> None:
        self.path.unlink(missing_ok=True)
        emit_write(self.path)


class JsonFacts:
    """A JSON list of id-addressable facts: `[{"id", "text", "ts"}, ...]`.

    Backs the curated long-term profile. Unlike `Blob` (replaced whole each turn),
    facts are mutated individually so the curator can ADD / UPDATE / DELETE one at a
    time without re-emitting the rest — the per-fact model. Ids are short, stable,
    and never reused, so an UPDATE/DELETE the curator emits keeps targeting the same
    fact across turns. Order is insertion order (oldest first).
    """

    def __init__(self, path: Path):
        self.path = Path(path)

    def read(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            # A corrupt/unreadable fact sheet must not break a chat — but silently
            # dropping the whole profile would hide real data loss, so warn.
            log_warning(
                f"memory: unreadable fact sheet {self.path.name}, dropping it "
                f"({type(exc).__name__}: {exc})"
            )
            return []
        return data if isinstance(data, list) else []

    def version(self) -> str:
        """A content version token for optimistic concurrency — a sha256 of the raw
        file bytes (empty-file token when absent). The admin viewer hands this to
        the operator and requires it back on a write, so a stale edit is a visible
        409 instead of a silent clobber of a concurrent curator write."""
        import hashlib

        raw = self.path.read_bytes() if self.path.exists() else b""
        return hashlib.sha256(raw).hexdigest()

    def texts(self) -> list[str]:
        """The fact bodies, in order (no ids) — for rendering into context."""
        return [str(f.get("text", "")) for f in self.read() if f.get("text")]

    def _write(self, facts: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")
        emit_write(self.path)

    def add(self, text: str) -> str:
        """Append one fact with a fresh id; return that id."""
        facts = self.read()
        fact_id = uuid.uuid4().hex[:8]
        facts.append({"id": fact_id, "text": text, "ts": _now()})
        self._write(facts)
        return fact_id

    def update(self, fact_id: str, text: str) -> bool:
        """Replace the text of `fact_id` in place. Returns whether it existed."""
        facts = self.read()
        for fact in facts:
            if fact.get("id") == fact_id:
                fact["text"] = text
                fact["ts"] = _now()
                self._write(facts)
                return True
        return False

    def remove(self, fact_id: str) -> bool:
        """Drop `fact_id`. Returns whether it existed."""
        facts = self.read()
        kept = [f for f in facts if f.get("id") != fact_id]
        if len(kept) == len(facts):
            return False
        self._write(kept)
        return True

    def trim(self, max_entries: int) -> int:
        """Keep only the newest `max_entries` facts (<= 0 disables). Returns dropped."""
        if max_entries <= 0:
            return 0
        facts = self.read()
        if len(facts) <= max_entries:
            return 0
        dropped = len(facts) - max_entries
        self._write(facts[-max_entries:])
        return dropped

    def delete(self) -> None:
        self.path.unlink(missing_ok=True)
        emit_write(self.path)
