"""Four file-shape adapters the memory store is built from.

Every memory file is one of these shapes; each adapter is constructed with a path
and owns the on-disk format for that shape. Nothing here knows about *kinds*
(long-term vs episodes vs ...) or scope — that lives one layer up in the store's
scope-bound bundle. Keeping the IO dumb is what makes memory auditable.

  - `BulletLog`   — append-only `- <ts> :: <content>` markdown (logs)
  - `Blob`        — single header + body, whole-file replace (summaries)
  - `JsonWindow`  — a JSON list of turn dicts (live window + pending buffer)
  - `JsonFacts`   — a JSON list of id-addressable facts (the curated profile)
"""

import json
import re
import uuid
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


class BulletLog:
    """Append-only markdown: a `# header` then `- <ts> :: <content>` bullets."""

    def __init__(self, path: Path, header: str):
        self.path = Path(path)
        self.header = header

    def _ensure_header(self) -> None:
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(f"# {self.header}\n\n", encoding="utf-8")

    def append(self, content: str) -> None:
        """Append one timestamped bullet."""
        self._ensure_header()
        line = f"- {_now()} :: {content.strip()}\n"
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line)

    def read(self) -> str:
        """The whole file, stripped (empty string when absent)."""
        if not self.path.exists():
            return ""
        return self.path.read_text(encoding="utf-8").strip()

    def bodies(self) -> list[str]:
        """The bullet bodies (content after `:: `), in order."""
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
        """Write a one-time initial body (header + scaffold) if the file is absent."""
        if self.path.exists():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(scaffold, encoding="utf-8")

    def delete(self) -> None:
        self.path.unlink(missing_ok=True)


class Blob:
    """A single `# header` + body file, replaced whole. Used for summaries."""

    def __init__(self, path: Path, header: str):
        self.path = Path(path)
        self.header = header

    def read(self) -> str:
        if not self.path.exists():
            return ""
        return self.path.read_text(encoding="utf-8").strip()

    def write(self, body: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(f"# {self.header}\n\n{body.strip()}\n", encoding="utf-8")

    def delete(self) -> None:
        self.path.unlink(missing_ok=True)


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

    def texts(self) -> list[str]:
        """The fact bodies, in order (no ids) — for rendering into context."""
        return [str(f.get("text", "")) for f in self.read() if f.get("text")]

    def _write(self, facts: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")

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
