"""Tests for the operator memory-edit module (core/memory/admin).

These pin the deeper seam directly: optimistic concurrency, fact mutation, raw
file validation, and semantic reconciliation.
"""

import pytest

from magi.core.memory.admin import (
    InvalidRawJsonError,
    MemoryManagerRequiredError,
    MemoryAdmin,
    SessionRequiredError,
    StaleVersionError,
    TriggerUnavailableError,
    UserRequiredError,
)
from magi.core.memory import build_memory
from magi.core.memory.store import FileMemoryStore


class _FakeRetriever:
    def __init__(self):
        self.reset_calls: list[tuple[str, str]] = []
        self.index_calls: list[tuple[str, str, str]] = []

    def index(self, user_id, kind, text):
        self.index_calls.append((user_id, kind, text))

    def search(self, user_id, query, kind, top_k):
        return []

    def reset(self, user_id, kind):
        self.reset_calls.append((user_id, kind))


def test_add_fact_reindexes_long_term_slice(tmp_path):
    store = FileMemoryStore(tmp_path)
    retriever = _FakeRetriever()
    admin = MemoryAdmin(store, retriever=retriever)

    result = admin.add_fact("u1", "likes tea", expected_version=None)

    assert [f.text for f in result.facts] == ["likes tea"]
    assert retriever.reset_calls == [("u1", "long_term")]
    assert retriever.index_calls == [("u1", "long_term", "likes tea")]


def test_update_fact_rejects_stale_version(tmp_path):
    store = FileMemoryStore(tmp_path)
    admin = MemoryAdmin(store)
    added = admin.add_fact("u1", "uses sqlite", expected_version=None)
    fact_id = added.facts[0].id

    with pytest.raises(StaleVersionError):
        admin.update_fact("u1", fact_id, "uses postgres", expected_version="stale")


def test_put_episode_file_reindexes_episode_slice(tmp_path):
    store = FileMemoryStore(tmp_path)
    retriever = _FakeRetriever()
    admin = MemoryAdmin(store, retriever=retriever)

    result = admin.put_raw_file("episodes", "# Episodic memory\n\n- shipped feature", None, user_id="u1")

    assert "shipped feature" in result.content
    assert retriever.reset_calls == [("u1", "episode")]
    assert retriever.index_calls == [("u1", "episode", "shipped feature")]


def test_put_session_window_requires_json_list(tmp_path):
    store = FileMemoryStore(tmp_path)
    admin = MemoryAdmin(store)

    with pytest.raises(InvalidRawJsonError):
        admin.put_raw_file("session_window", "{}", None, user_id="u1", session_id="s1")


def test_session_file_requires_session_id(tmp_path):
    admin = MemoryAdmin(FileMemoryStore(tmp_path))

    with pytest.raises(SessionRequiredError):
        admin.get_raw_file("session_window", user_id="u1")


def test_user_scoped_file_requires_user_id(tmp_path):
    admin = MemoryAdmin(FileMemoryStore(tmp_path))

    with pytest.raises(UserRequiredError) as exc:
        admin.get_raw_file("episodes")
    assert "user_id required" in str(exc.value)


def test_session_snapshot_returns_turns_summary_and_pending(tmp_path):
    store = FileMemoryStore(tmp_path)
    mem = store.scoped("u1", "s1")
    mem.live_turns.append("user", "hi", 20)
    mem.live_turns.append("assistant", "hello", 20)
    mem.session_summary.write("earlier summary")
    mem.pending.extend([{"role": "user", "content": "older", "ts": "t1"}])

    snapshot = MemoryAdmin(store).session("u1", "s1")

    assert [(t.role, t.content) for t in snapshot.turns] == [("user", "hi"), ("assistant", "hello")]
    # Blob.read() strips frontmatter but keeps the `# header`, so the body is the tail.
    assert snapshot.summary.endswith("earlier summary")
    assert snapshot.pending[0].content == "older"


@pytest.mark.anyio
async def test_summarize_session_requires_manager(tmp_path):
    admin = MemoryAdmin(FileMemoryStore(tmp_path))

    with pytest.raises(MemoryManagerRequiredError):
        await admin.summarize_session(None, "u1", "s1")


@pytest.mark.anyio
async def test_curate_session_reconciles_profile_changes(tmp_path):
    seen = {}

    async def fake_curate(inp):
        seen["user_message"] = inp.user_message
        from magi.core.memory import CurationResult, FactOp

        return CurationResult(operations=(FactOp(op="add", text="prefers dark mode"),), episode=None)

    store = FileMemoryStore(tmp_path)
    mem = store.scoped("u1", "s1")
    mem.session_summary.write("rolled summary")
    retriever = _FakeRetriever()
    manager = build_memory(store=store, short_term_max=20, curate_fn=fake_curate)

    result = await MemoryAdmin(store, retriever=retriever).curate_session(manager, "u1", "s1")

    assert result.action == "curate"
    assert result.changed is True
    # The curator receives the session summary as read (frontmatter stripped, `# header` kept).
    assert seen["user_message"].endswith("rolled summary")
    assert retriever.reset_calls == [("u1", "long_term")]
    assert retriever.index_calls == [("u1", "long_term", "prefers dark mode")]


@pytest.mark.anyio
async def test_summarize_session_unavailable_without_model(tmp_path):
    store = FileMemoryStore(tmp_path)
    manager = build_memory(store=store, short_term_max=20)

    with pytest.raises(TriggerUnavailableError):
        await MemoryAdmin(store).summarize_session(manager, "u1", "s1")