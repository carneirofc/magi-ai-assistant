"""Tests for the deliberate filesystem memory (core/memory).

These guard the contract the model depends on: writes land in plain markdown
files, scope routes them to the right user/session, short-term stays capped, and
`build_context` assembles only the non-empty sections.
"""

import pytest

from core.memory import manager as manager_mod
from core.memory.manager import MemoryManager
from core.memory.store import FileMemoryStore


@pytest.fixture(autouse=True)
def _reset_scope():
    """Scope is a process-global ContextVar; clear it between tests."""
    token = manager_mod._scope.set(None)
    yield
    manager_mod._scope.reset(token)


@pytest.fixture
def manager(tmp_path):
    mgr = MemoryManager(
        store=FileMemoryStore(tmp_path / "memory"),
        short_term_max=3,
        persona_seed="You are Alyssa.",
    )
    mgr.set_scope(user_id="u1", session_id="s1")
    return mgr


def test_remember_writes_long_term(manager):
    manager.remember("User prefers Python.")
    assert "User prefers Python." in manager.recall_long_term()


def test_long_term_is_scoped_per_user(manager):
    manager.remember("fact for u1")
    manager.set_scope(user_id="u2", session_id="s9")
    assert "fact for u1" not in manager.recall_long_term()


def test_episodes_recorded_and_limited(manager):
    for i in range(5):
        manager.record_episode(f"episode {i}")
    recalled = manager.recall_episodes(limit=2)
    assert "episode 4" in recalled and "episode 3" in recalled
    assert "episode 0" not in recalled


def test_short_term_window_is_capped(manager):
    for i in range(5):
        manager.record_user_turn(f"msg {i}")
    s = manager.scope()
    turns = manager.store.read_turns(s.user_id, s.session_id)
    contents = [t["content"] for t in turns]
    assert len(turns) == 3  # short_term_max
    assert "msg 4" in contents and "msg 1" not in contents


def test_evolve_persona_appends_and_persists(manager):
    manager.evolve_persona("Be more concise on Discord.")
    assert "Be more concise on Discord." in manager.store.read_persona()
    # Seed persona is preserved alongside the evolution.
    assert "You are Alyssa." in manager.store.read_persona()


def test_build_context_includes_written_memory(manager):
    manager.remember("User is a CTF player.")
    manager.record_episode("Helped debug auth middleware.")
    manager.record_user_turn("hello")
    ctx = manager.build_context()
    assert "You are Alyssa." in ctx
    assert "User is a CTF player." in ctx
    assert "Helped debug auth middleware." in ctx
    assert "hello" in ctx


def test_build_context_omits_empty_sections(manager):
    ctx = manager.build_context()
    assert "What you remember about this user" not in ctx
    assert "Past episodes" not in ctx


def test_scope_required_before_use(tmp_path):
    mgr = MemoryManager(FileMemoryStore(tmp_path), short_term_max=5)
    with pytest.raises(RuntimeError):
        mgr.remember("no scope set")


# --- flush + monitoring -----------------------------------------------------


def test_flush_clears_short_term_keeps_long_term(manager):
    manager.remember("durable fact")
    for i in range(3):
        manager.record_user_turn(f"turn {i}")
    dropped = manager.flush_session()
    assert dropped == 3
    s = manager.scope()
    assert manager.store.read_turns(s.user_id, s.session_id) == []
    # Long-term survives the flush.
    assert "durable fact" in manager.recall_long_term()


def test_context_stats_reports_sections_and_tokens(manager):
    manager.remember("User loves Python.")
    manager.record_user_turn("hi there")
    stats = manager.context_stats()
    assert stats["est_tokens"] > 0
    assert stats["short_term_turns"] == 1
    assert stats["sections"]["long_term"] > 0
    assert 0 <= stats["ratio"] <= 1


# --- session + long-term summarization --------------------------------------


async def test_session_summary_rolls_up_and_folds_into_episode(tmp_path):
    calls = []

    async def fake_summarize(text: str) -> str:
        calls.append(text)
        return "rolling session summary"

    mgr = MemoryManager(
        store=FileMemoryStore(tmp_path / "mem"),
        short_term_max=3,
        summarize_session_fn=fake_summarize,
        summarize_every=2,
    )
    mgr.set_scope(user_id="u1", session_id="s1")

    # 5 turns with a window of 3 => 2 evicted and buffered (>= summarize_every).
    for i in range(5):
        mgr.record_user_turn(f"msg {i}")

    summary = await mgr.maybe_summarize_session()
    assert summary == "rolling session summary"
    assert calls, "summarizer should have been called"
    s = mgr.scope()
    assert "rolling session summary" in mgr.store.read_session_summary(s.user_id, s.session_id)
    assert mgr.store.count_pending(s.user_id, s.session_id) == 0  # buffer drained

    # Closing the session folds the rolling summary into a global episode.
    mgr.flush_session()
    assert "rolling session summary" in mgr.recall_episodes()


async def test_no_session_summary_below_threshold(tmp_path):
    async def fake_summarize(text: str) -> str:  # pragma: no cover - must not run
        raise AssertionError("should not summarize below threshold")

    mgr = MemoryManager(
        store=FileMemoryStore(tmp_path / "mem"),
        short_term_max=3,
        summarize_session_fn=fake_summarize,
        summarize_every=5,
    )
    mgr.set_scope(user_id="u1", session_id="s1")
    for i in range(4):  # only 1 evicted, below summarize_every=5
        mgr.record_user_turn(f"msg {i}")
    assert await mgr.maybe_summarize_session() is None


def test_eviction_without_summarizer_just_drops(manager):
    """No summarizer => old turns drop, nothing buffered (prior behavior)."""
    for i in range(5):  # window is 3
        manager.record_user_turn(f"msg {i}")
    s = manager.scope()
    assert manager.store.count_pending(s.user_id, s.session_id) == 0
    assert manager.recall_episodes() == "(no episodes recorded yet)"


async def test_long_term_summary_written_and_injected(tmp_path):
    async def fake_summarize(text: str) -> str:
        return "condensed profile"

    mgr = MemoryManager(
        store=FileMemoryStore(tmp_path / "mem"),
        short_term_max=5,
        summarize_long_term_fn=fake_summarize,
        long_term_summarize_every=3,
        long_term_recent_raw=2,
    )
    mgr.set_scope(user_id="u1", session_id="s1")
    for i in range(3):
        mgr.remember(f"fact {i}")

    out = await mgr.maybe_summarize_long_term()
    assert out == "condensed profile"
    s = mgr.scope()
    assert "condensed profile" in mgr.store.read_long_term_summary(s.user_id)

    # Context injects the summary plus only the most-recent raw facts.
    ctx = mgr.build_context()
    assert "condensed profile" in ctx
    assert "fact 2" in ctx  # within recent-raw tail (last 2)
    assert "fact 0" not in ctx  # older facts live only in the summary now


# --- semantic retrieval (fake retriever) ------------------------------------


class _FakeRetriever:
    def __init__(self, hits):
        self.hits = hits
        self.indexed = []

    def index(self, user_id, kind, text):
        self.indexed.append((user_id, kind, text))

    def search(self, user_id, query, kind, top_k):
        return self.hits.get(kind, [])


def test_retriever_indexes_and_overrides_context(tmp_path):
    retriever = _FakeRetriever({"long_term": ["relevant fact"], "episode": ["relevant episode"]})
    mgr = MemoryManager(
        FileMemoryStore(tmp_path / "mem"), short_term_max=5, retriever=retriever
    )
    mgr.set_scope(user_id="u1", session_id="s1")
    mgr.remember("some stored fact")
    assert ("u1", "long_term", "some stored fact") in retriever.indexed

    ctx = mgr.build_context(query="anything")
    assert "relevant fact" in ctx
    assert "relevant episode" in ctx


def test_retriever_empty_falls_back_to_whole_file(tmp_path):
    retriever = _FakeRetriever({})  # search returns []
    mgr = MemoryManager(
        FileMemoryStore(tmp_path / "mem"), short_term_max=5, retriever=retriever
    )
    mgr.set_scope(user_id="u1", session_id="s1")
    mgr.remember("the whole-file fact")
    ctx = mgr.build_context(query="anything")
    assert "the whole-file fact" in ctx
