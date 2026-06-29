"""The subject registry — the controlled vocabulary for grouping knowledge docs.

Subjects are the single, low-cardinality spine the admin curates (create / rename /
delete) and each document picks one of. Unlike free-form tags (derived from the
corpus), subjects persist independently of documents so one can exist before any
doc is filed under it, and be renamed in one place. A tiny JSON file backs it:

    [{"id": "ab12cd34", "name": "Infra", "description": "..."}]

Documents store the subject *name* on their chunks (so the model filters by a
human label, not an opaque id); a registry rename therefore cascades to the corpus
(see `KnowledgeStore.rename_subject`), kept in lockstep by the admin layer.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from agno.utils.log import log_warning


@dataclass(frozen=True)
class Subject:
    id: str
    name: str
    description: str = ""


class SubjectRegistry:
    """A JSON-file list of subjects with CRUD. Read is crash-proof (a corrupt file
    degrades to empty with a warning, never breaks the admin app)."""

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def _read(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log_warning(f"subjects: unreadable registry {self.path.name} ({type(exc).__name__}: {exc})")
            return []
        return data if isinstance(data, list) else []

    def _write(self, subjects: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(subjects, ensure_ascii=False, indent=2), encoding="utf-8")

    def list(self) -> list[Subject]:
        return [
            Subject(id=str(s.get("id", "")), name=str(s.get("name", "")), description=str(s.get("description", "")))
            for s in self._read()
            if s.get("id") and s.get("name")
        ]

    def get(self, subject_id: str) -> Optional[Subject]:
        return next((s for s in self.list() if s.id == subject_id), None)

    def create(self, name: str, description: str = "") -> Optional[Subject]:
        """Add a subject. Names are unique (case-insensitive); a duplicate returns
        None rather than creating a second bucket."""
        name = name.strip()
        if not name:
            return None
        subjects = self._read()
        if any(str(s.get("name", "")).lower() == name.lower() for s in subjects):
            return None
        subject = Subject(id=uuid.uuid4().hex[:8], name=name, description=description.strip())
        subjects.append({"id": subject.id, "name": subject.name, "description": subject.description})
        self._write(subjects)
        return subject

    def rename(
        self, subject_id: str, name: Optional[str] = None, description: Optional[str] = None
    ) -> Optional[Subject]:
        """Edit a subject's name/description in place. Returns the updated subject,
        or None when the id is unknown."""
        subjects = self._read()
        for s in subjects:
            if s.get("id") == subject_id:
                if name is not None and name.strip():
                    s["name"] = name.strip()
                if description is not None:
                    s["description"] = description.strip()
                self._write(subjects)
                return Subject(id=subject_id, name=str(s["name"]), description=str(s.get("description", "")))
        return None

    def delete(self, subject_id: str) -> bool:
        subjects = self._read()
        kept = [s for s in subjects if s.get("id") != subject_id]
        if len(kept) == len(subjects):
            return False
        self._write(kept)
        return True
