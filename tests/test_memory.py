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
    turns = manager.mem.live_turns.read()
    contents = [t["content"] for t in turns]
    assert len(turns) == 3  # short_term_max
    assert "msg 4" in contents and "msg 1" not in contents


def test_evolve_persona_appends_and_persists(manager):
    manager.evolve_persona("Be more concise on Discord.")
    assert "Be more concise on Discord." in manager.store.persona.read()
    # Seed persona is preserved alongside the evolution.
    assert "You are Alyssa." in manager.store.persona.read()


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
    assert manager.mem.live_turns.read() == []
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
    assert "rolling session summary" in mgr.mem.session_summary.read()
    assert mgr.mem.pending.count() == 0  # buffer drained

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


async def test_session_summary_failure_never_breaks_the_chat(tmp_path):
    async def boom(text: str) -> str:
        raise RuntimeError("summarizer down")

    mgr = MemoryManager(
        store=FileMemoryStore(tmp_path / "mem"),
        short_term_max=3,
        summarize_session_fn=boom,
        summarize_every=2,
    )
    mgr.set_scope(user_id="u1", session_id="s1")
    for i in range(5):
        mgr.record_user_turn(f"msg {i}")

    # Summarizer raised, but the call swallows it and keeps the buffer intact.
    assert await mgr.maybe_summarize_session() is None
    assert mgr.mem.pending.count() >= 2


# --- size guards (short-term must not explode the context) ------------------


def test_huge_turn_is_clamped(tmp_path):
    mgr = MemoryManager(
        store=FileMemoryStore(tmp_path / "mem"),
        short_term_max=3,
        short_term_turn_max_chars=100,
    )
    mgr.set_scope(user_id="u1", session_id="s1")
    mgr.record_user_turn("x" * 10_000)

    [turn] = mgr.mem.live_turns.read()
    assert len(turn["content"]) < 200  # 100 + truncation marker
    assert "truncated" in turn["content"]


async def test_pending_buffer_capped_when_summarizer_keeps_failing(tmp_path):
    async def boom(text: str) -> str:
        raise RuntimeError("summarizer down")

    mgr = MemoryManager(
        store=FileMemoryStore(tmp_path / "mem"),
        short_term_max=2,
        summarize_session_fn=boom,
        summarize_every=2,
        session_pending_max=4,
    )
    mgr.set_scope(user_id="u1", session_id="s1")
    for i in range(20):  # 18 evictions, every fold attempt fails
        mgr.record_user_turn(f"msg {i}")
        await mgr.maybe_summarize_session()

    assert mgr.mem.pending.count() <= 4  # oldest dropped, no unbounded growth


async def test_runaway_session_summary_is_clamped(tmp_path):
    async def huge(text: str) -> str:
        return "s" * 50_000

    mgr = MemoryManager(
        store=FileMemoryStore(tmp_path / "mem"),
        short_term_max=2,
        summarize_session_fn=huge,
        summarize_every=2,
        session_summary_max_chars=500,
    )
    mgr.set_scope(user_id="u1", session_id="s1")
    for i in range(4):  # 2 evicted => fold fires
        mgr.record_user_turn(f"msg {i}")
    await mgr.maybe_summarize_session()

    stored = mgr.mem.session_summary.read()
    assert len(stored) < 700  # 500 + header + marker
    assert "truncated" in stored


def test_eviction_without_summarizer_just_drops(manager):
    """No summarizer => old turns drop, nothing buffered (prior behavior)."""
    for i in range(5):  # window is 3
        manager.record_user_turn(f"msg {i}")
    assert manager.mem.pending.count() == 0
    assert manager.recall_episodes() == "(no episodes recorded yet)"


def test_long_term_profile_and_recent_raw_injected(tmp_path):
    """build_context renders the curated fact sheet (long_term_facts, owned by the
    curator) plus only the most-recent raw facts written via remember()."""
    mgr = MemoryManager(
        store=FileMemoryStore(tmp_path / "mem"),
        short_term_max=5,
        long_term_recent_raw=2,
    )
    mgr.set_scope(user_id="u1", session_id="s1")
    for i in range(3):
        mgr.remember(f"fact {i}")
    # The curator owns this file; simulate a curation pass adding a durable fact.
    mgr.mem.long_term_facts.add("condensed profile")

    ctx = mgr.build_context()
    assert "condensed profile" in ctx
    assert "fact 2" in ctx  # within recent-raw tail (last 2)
    assert "fact 0" not in ctx  # older raw fact trimmed (lives only in the sheet now)


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
