"""Operator-facing memory editing behind one deeper module.

This module owns the admin memory-edit seam: optimistic-concurrency checks,
fact mutation, raw-file reads/writes, and semantic/archive reconciliation.
`channels/admin.py` is the HTTP adapter over this interface; it should not need
to know how a fact version is derived or how semantic slices are rebuilt.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from magi.core.items import ItemArchive
from magi.core.memory.adapters import slug as _mem_slug
from magi.core.memory.manager import MemoryManager
from magi.core.memory.semantic import MemoryRetriever
from magi.core.memory.store import FileMemoryStore, ScopedMemory

_USER_SCOPE_SID = "_admin"
_LONG_TERM_KIND = "long_term"
_EPISODE_KIND = "episode"


class StaleVersionError(Exception):
    """The caller wrote against an out-of-date version token."""


class UnknownMemoryFileKindError(Exception):
    """The requested raw memory file kind is not supported."""


class SessionRequiredError(Exception):
    """The requested raw memory file kind requires a session id."""


class InvalidRawJsonError(Exception):
    """A JSON-backed raw memory file failed validate-on-save."""


class UserRequiredError(Exception):
    """The requested raw memory file kind requires a user id."""


class MemoryManagerRequiredError(Exception):
    """Operator-triggered passes require a wired memory manager."""


class TriggerUnavailableError(Exception):
    """The requested operator-triggered pass is unavailable in this deployment."""


@dataclass(frozen=True)
class FactRecord:
    id: str
    text: str
    ts: str = ""


@dataclass(frozen=True)
class ProfileSnapshot:
    facts: list[FactRecord]
    raw_long_term: list[str]
    episodes: list[str]
    version: str


@dataclass(frozen=True)
class FactsSnapshot:
    facts: list[FactRecord]
    version: str


@dataclass(frozen=True)
class RawFileSnapshot:
    kind: str
    content: str
    version: str


@dataclass(frozen=True)
class TurnRecord:
    role: str = ""
    content: str = ""
    ts: str = ""


@dataclass(frozen=True)
class SessionSnapshot:
    turns: list[TurnRecord]
    summary: str
    pending: list[TurnRecord]


@dataclass(frozen=True)
class TriggerSnapshot:
    action: str
    changed: bool
    detail: str


@dataclass(frozen=True)
class RawFileTarget:
    path: Path
    is_json: bool
    reindex_kind: Optional[str]
    reindex_user_id: Optional[str]
    reindex_session_id: Optional[str]


class MemoryAdmin:
    """One deeper module for operator memory reads and writes."""

    def __init__(
        self,
        memory: FileMemoryStore,
        *,
        retriever: Optional[MemoryRetriever] = None,
        archive: Optional[ItemArchive] = None,
    ) -> None:
        self.memory = memory
        self.retriever = retriever
        self.archive = archive

    def list_users(self) -> list[str]:
        return self.memory.list_users()

    def list_sessions(self, user_id: str) -> list[str]:
        return self.memory.list_sessions(user_id)

    def profile(self, user_id: str) -> ProfileSnapshot:
        mem = self._user_mem(user_id)
        return ProfileSnapshot(
            facts=self._facts(mem),
            raw_long_term=mem.long_term.bodies(),
            episodes=mem.episodes.bodies(),
            version=mem.long_term_facts.version(),
        )

    def add_fact(self, user_id: str, text: str, expected_version: Optional[str]) -> FactsSnapshot:
        mem = self._user_mem(user_id)
        self._check_fact_version(mem, expected_version)
        mem.long_term_facts.add(text.strip())
        self._sync_long_term(mem)
        return self.facts_snapshot(user_id)

    def update_fact(
        self, user_id: str, fact_id: str, text: str, expected_version: Optional[str]
    ) -> Optional[FactsSnapshot]:
        mem = self._user_mem(user_id)
        self._check_fact_version(mem, expected_version)
        if not mem.long_term_facts.update(fact_id, text.strip()):
            return None
        self._sync_long_term(mem)
        return self.facts_snapshot(user_id)

    def delete_fact(
        self, user_id: str, fact_id: str, expected_version: Optional[str]
    ) -> Optional[FactsSnapshot]:
        mem = self._user_mem(user_id)
        self._check_fact_version(mem, expected_version)
        if not mem.long_term_facts.remove(fact_id):
            return None
        self._sync_long_term(mem)
        return self.facts_snapshot(user_id)

    def facts_snapshot(self, user_id: str) -> FactsSnapshot:
        mem = self._user_mem(user_id)
        return FactsSnapshot(facts=self._facts(mem), version=mem.long_term_facts.version())

    def session(self, user_id: str, session_id: str) -> SessionSnapshot:
        mem = self.memory.scoped(user_id, session_id)
        return SessionSnapshot(
            turns=[self._turn_record(t) for t in mem.live_turns.read()],
            summary=mem.session_summary.read(),
            pending=[self._turn_record(t) for t in mem.pending.read()],
        )

    async def summarize_session(self, manager: Optional[MemoryManager], user_id: str, session_id: str) -> TriggerSnapshot:
        mgr = self._require_manager(manager)
        if not mgr.session_summary_enabled:
            raise TriggerUnavailableError(
                "session summarization unavailable in this deployment (no model wired)"
            )
        mgr.set_scope(user_id, session_id)
        summary = await mgr.summarize_session_now()
        return TriggerSnapshot(
            action="summarize",
            changed=summary is not None,
            detail=(
                "Folded the pending buffer into the rolling session summary."
                if summary is not None
                else "Nothing pending to summarize."
            ),
        )

    async def curate_session(self, manager: Optional[MemoryManager], user_id: str, session_id: str) -> TriggerSnapshot:
        mgr = self._require_manager(manager)
        if not mgr.curation_enabled:
            raise TriggerUnavailableError(
                "memory curation unavailable in this deployment (no model wired)"
            )
        mgr.set_scope(user_id, session_id)
        applied = await mgr.curate_session_summary()
        if applied and "profile" in applied:
            self._sync_long_term(self._user_mem(user_id))
        return TriggerSnapshot(
            action="curate",
            changed=bool(applied),
            detail=(
                f"Curated durable memory: {', '.join(applied)}."
                if applied
                else "No durable changes (empty session summary or nothing to keep)."
            ),
        )

    async def flush_session(self, manager: Optional[MemoryManager], user_id: str, session_id: str) -> TriggerSnapshot:
        mgr = self._require_manager(manager)
        mgr.set_scope(user_id, session_id)
        dropped = mgr.flush_session()
        return TriggerSnapshot(
            action="flush",
            changed=dropped > 0,
            detail=(
                f"Flushed {dropped} live turn(s); the rolling summary was carried into an episode."
                if dropped
                else "Nothing to flush (no live turns)."
            ),
        )

    def get_raw_file(
        self, kind: str, *, user_id: Optional[str] = None, session_id: Optional[str] = None
    ) -> RawFileSnapshot:
        target = self.raw_target(kind, user_id=user_id, session_id=session_id)
        content = target.path.read_text(encoding="utf-8") if target.path.exists() else ""
        return RawFileSnapshot(kind=kind, content=content, version=self.file_version(target.path))

    def put_raw_file(
        self,
        kind: str,
        content: str,
        expected_version: Optional[str],
        *,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> RawFileSnapshot:
        target = self.raw_target(kind, user_id=user_id, session_id=session_id)
        self._check_file_version(target.path, expected_version)
        if target.is_json:
            self._validate_json_list(content)
        target.path.parent.mkdir(parents=True, exist_ok=True)
        target.path.write_text(content, encoding="utf-8")
        self._sync_raw_target(target)
        return RawFileSnapshot(kind=kind, content=content, version=self.file_version(target.path))

    def raw_target(
        self, kind: str, *, user_id: Optional[str], session_id: Optional[str]
    ) -> RawFileTarget:
        if kind == "persona":
            return RawFileTarget(self.memory.persona.path, False, None, None, None)
        if not user_id:
            raise UserRequiredError("user_id required for user-scoped memory file")
        mem = self.memory.scoped(user_id, session_id or _USER_SCOPE_SID)
        if kind == "episodes":
            return RawFileTarget(mem.episodes.path, False, _EPISODE_KIND, user_id, session_id)
        if kind == "raw_long_term":
            return RawFileTarget(mem.long_term.path, False, None, None, None)
        if kind in ("session_window", "session_summary", "session_pending"):
            if not session_id:
                raise SessionRequiredError("session_id required for this kind")
            mapping = {
                "session_window": RawFileTarget(mem.live_turns.path, True, None, None, None),
                "session_summary": RawFileTarget(mem.session_summary.path, False, None, None, None),
                "session_pending": RawFileTarget(mem.pending.path, True, None, None, None),
            }
            return mapping[kind]
        raise UnknownMemoryFileKindError(kind)

    @staticmethod
    def file_version(path: Path) -> str:
        raw = path.read_bytes() if path.exists() else b""
        return hashlib.sha256(raw).hexdigest()

    def _user_mem(self, user_id: str) -> ScopedMemory:
        return self.memory.scoped(user_id, _USER_SCOPE_SID)

    @staticmethod
    def _turn_record(turn: dict) -> TurnRecord:
        return TurnRecord(
            role=str(turn.get("role", "")),
            content=str(turn.get("content", "")),
            ts=str(turn.get("ts", "")),
        )

    @staticmethod
    def _require_manager(manager: Optional[MemoryManager]) -> MemoryManager:
        if manager is None:
            raise MemoryManagerRequiredError(
                "memory triggers unavailable: no memory manager wired into this admin app"
            )
        return manager

    @staticmethod
    def _facts(mem: ScopedMemory) -> list[FactRecord]:
        return [
            FactRecord(
                id=str(f.get("id", "")),
                text=str(f.get("text", "")),
                ts=str(f.get("ts", "")),
            )
            for f in mem.long_term_facts.read()
        ]

    def _check_fact_version(self, mem: ScopedMemory, expected: Optional[str]) -> None:
        if expected is not None and expected != mem.long_term_facts.version():
            raise StaleVersionError("stale version; refetch the profile")

    def _check_file_version(self, path: Path, expected: Optional[str]) -> None:
        if expected is not None and expected != self.file_version(path):
            raise StaleVersionError("stale version; refetch the file")

    @staticmethod
    def _validate_json_list(content: str) -> None:
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise InvalidRawJsonError(f"invalid JSON: {exc}") from exc
        if not isinstance(parsed, list):
            raise InvalidRawJsonError("expected a JSON list of turns")

    def _sync_long_term(self, mem: ScopedMemory) -> None:
        if self.retriever is not None:
            self.retriever.reset(mem.user_id, _LONG_TERM_KIND)
            for text in mem.long_term_facts.texts():
                self.retriever.index(mem.user_id, _LONG_TERM_KIND, text)
        if self.archive is not None:
            path = mem.long_term_facts.path
            data = path.read_bytes() if path.exists() else b"[]"
            self.archive.persist(
                "memory",
                _mem_slug(mem.user_id),
                data=data,
                content_type="application/json",
                metadata={"file": "long_term_facts.json", "user_id": mem.user_id},
            )

    def _sync_raw_target(self, target: RawFileTarget) -> None:
        if (
            target.reindex_kind == _EPISODE_KIND
            and self.retriever is not None
            and target.reindex_user_id is not None
        ):
            mem = self.memory.scoped(target.reindex_user_id, target.reindex_session_id or _USER_SCOPE_SID)
            self.retriever.reset(target.reindex_user_id, _EPISODE_KIND)
            for body in mem.episodes.bodies():
                self.retriever.index(target.reindex_user_id, _EPISODE_KIND, body)