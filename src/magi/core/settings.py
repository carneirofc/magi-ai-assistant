"""Operator settings — the small set of runtime overrides an operator edits from the
admin UI, layered over the code-first `Config` defaults.

The app stays code-first: `Config` holds the defaults, set at the entrypoint via
`configure(...)`. A few settings, though, are operational rather than code-shaped —
most notably *where memory lives on disk* and whether it's git-versioned — and an
operator needs to change those without editing `main.py`. This module persists those
overrides in a small JSON file **outside** the memory tree (it can't live inside the
directory it points at) and hands back what was set; the memory factory overlays them
on `config` at startup, and the admin API reads/writes them.

Only fields the operator actually set are stored — an unset field falls through to the
`Config` default at resolve time (see `magi/core/memory`). Pure IO, model-free, like
`IdentityStore`: it reads/writes the file and versions it for optimistic concurrency;
it does not know about `config` (resolution lives in the memory factory, which does).
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from magi.core.types import JsonObject, parse_json_object


def _has_name(entry: JsonObject) -> bool:
    """An MCP entry is keyed by a non-blank string `name`; others are dropped."""
    name = entry.get("name")
    return isinstance(name, str) and bool(name.strip())


@dataclass(frozen=True)
class MemoryOverrides:
    """Operator overrides for the memory subsystem. Every field is optional — `None`
    means "inherit the code default" (the factory resolves it against `config`)."""

    memory_dir: str | None = None
    git_enabled: bool | None = None
    git_author_name: str | None = None
    git_author_email: str | None = None

    @property
    def is_empty(self) -> bool:
        return (
            self.memory_dir is None
            and self.git_enabled is None
            and self.git_author_name is None
            and self.git_author_email is None
        )


class _MemorySection(BaseModel):
    """The `memory` section as stored on disk. Strict: a wrong-typed field makes
    the whole section fall back to the code defaults rather than coercing."""

    model_config = ConfigDict(extra="ignore", strict=True)

    memory_dir: str | None = None
    git_enabled: bool | None = None
    git_author_name: str | None = None
    git_author_email: str | None = None


class OperatorSettingsStore:
    """The operator-editable settings on disk: a single JSON file, versioned.

    Layout (namespaced so future setting groups can be added beside `memory`):

        {
          "memory": {
            "memory_dir": "...",
            "git_enabled": true,
            "git_author_name": "...",
            "git_author_email": "..."
          }
        }

    A missing/corrupt file reads as "no overrides" — never raises on read, so a bad
    file degrades to code defaults instead of breaking startup.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    # --- reads --------------------------------------------------------------
    def _read_json(self) -> JsonObject:
        """The parsed settings, or `{}` when absent/corrupt (never raises)."""
        if not self.path.exists():
            return {}
        try:
            return parse_json_object(self.path.read_bytes()) or {}
        except OSError:
            return {}

    def read_memory(self) -> MemoryOverrides:
        """The persisted memory overrides (all-`None` when nothing is set)."""
        try:
            section = _MemorySection.model_validate(self._read_json().get("memory"))
        except ValidationError:
            return MemoryOverrides()
        # A blank string is treated as "unset" so clearing a field in the UI falls
        # back to the code default rather than pinning an empty path/name.
        return MemoryOverrides(
            memory_dir=(section.memory_dir or "").strip() or None,
            git_enabled=section.git_enabled,
            git_author_name=(section.git_author_name or "").strip() or None,
            git_author_email=(section.git_author_email or "").strip() or None,
        )

    def read_mcp(self) -> list[JsonObject]:
        """The operator-added MCP server specs (see config.mcp_servers for the
        shape), or [] when none are set. Entries merge over the code list by
        name at team assembly (magi/agent/tools/mcp.py)."""
        section = self._read_json().get("mcp")
        if not isinstance(section, list):
            return []
        return [e for e in section if isinstance(e, dict) and _has_name(e)]

    def version(self) -> str:
        """Optimistic-concurrency token over the raw file bytes (empty token when
        absent). The admin editor echoes it on a write and gets a 409 if it's stale,
        matching the identity/facts endpoints."""
        raw = self.path.read_bytes() if self.path.exists() else b""
        return hashlib.sha256(raw).hexdigest()

    # --- writes -------------------------------------------------------------
    def set_memory(self, overrides: MemoryOverrides) -> MemoryOverrides:
        """Persist the memory overrides, replacing that section. `None` fields are
        dropped from the file (so they resolve to the code default). Returns the
        stored overrides (as read back)."""
        data = self._read_json()
        section: dict[str, object] = {}
        if overrides.memory_dir is not None:
            section["memory_dir"] = overrides.memory_dir
        if overrides.git_enabled is not None:
            section["git_enabled"] = overrides.git_enabled
        if overrides.git_author_name is not None:
            section["git_author_name"] = overrides.git_author_name
        if overrides.git_author_email is not None:
            section["git_author_email"] = overrides.git_author_email
        if section:
            data["memory"] = section
        else:
            data.pop("memory", None)
        self._write_json(data)
        return self.read_memory()

    def set_mcp(self, servers: list[JsonObject]) -> list[JsonObject]:
        """Persist the operator MCP server list, replacing that section (an
        empty list clears it). The team reads the merge at startup — changes
        apply on restart. Returns the stored list (as read back)."""
        data = self._read_json()
        cleaned = [dict(e) for e in servers if _has_name(e)]
        if cleaned:
            data["mcp"] = cleaned
        else:
            data.pop("mcp", None)
        self._write_json(data)
        return self.read_mcp()

    def _write_json(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
