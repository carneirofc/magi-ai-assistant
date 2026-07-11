"""Tests for the session archive + context budgets (P: previous-chat reference
and context management).

Three seams: `FileMemoryStore.session_overview`/`search_history` (pure file
scans over the memory tree), the `/v1/sessions*` archive endpoints (wire
contract over a real store), and the assembly-time guardrails
(`context_section_budgets`, pressure-triggered fold).
"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from magi.channels.api import create_app
from magi.core.config import config, configure
from magi.core.memory import manager as manager_mod
from magi.core.memory.manager import MemoryManager
from magi.core.memory.store import FileMemoryStore


@pytest.fixture(autouse=True)
def _reset_scope():
    token = manager_mod._scope.set(None)
    yield
    manager_mod._scope.reset(token)


def _stamp(window, ts: str) -> None:
    """Pin a window's turn timestamps (appends within one test share a second,
    which would make newest-first ordering a coin flip)."""
    turns = window.read()
    for turn in turns:
        turn["ts"] = ts
    window._write(turns)


def _seeded_store(tmp_path, user: str = "u1") -> FileMemoryStore:
    """A memory tree with two sessions, a summary, and an episode for `user`."""
    store = FileMemoryStore(tmp_path / "memory")
    s1 = store.scoped(user, "s1")
    s1.live_turns.append("user", "let's plan the docker build", 10)
    s1.live_turns.append("assistant", "start from a slim base image", 10)
    _stamp(s1.live_turns, "2026-07-01T10:00:00")
    s2 = store.scoped(user, "s2")
    s2.live_turns.append("user", "remind me about the anime schedule", 10)
    _stamp(s2.live_turns, "2026-07-02T10:00:00")
    s2.session_summary.write("Talked about airing schedules.")
    s1.episodes.append("Helped debug a Dockerfile; user prefers slim images.")
    return store


# --- store scans ---------------------------------------------------------------


def test_session_overview_lists_sessions_with_metadata(tmp_path):
    store = _seeded_store(tmp_path)

    overview = store.session_overview("u1")

    assert [s["id"] for s in overview] == ["s2", "s1"]  # newest activity first
    s2 = overview[0]
    assert (s2["turns"], s2["has_summary"]) == (1, True)
    assert s2["preview"].startswith("remind me")
    assert overview[1]["last_ts"]  # turns carry timestamps


def test_search_history_finds_transcripts_summaries_and_episodes(tmp_path):
    store = _seeded_store(tmp_path)

    kinds = {h["kind"]: h for h in store.search_history("u1", "DOCKER", limit=10)}
    assert "transcript" in kinds and kinds["transcript"]["session_id"] == "s1"
    assert kinds["transcript"]["role"] == "user"
    assert "docker" in kinds["transcript"]["snippet"]

    assert store.search_history("u1", "airing", limit=10)[0]["kind"] == "summary"
    episode_hits = store.search_history("u1", "slim images", limit=10)
    assert any(h["kind"] == "episode" for h in episode_hits)
    assert store.search_history("u1", "nothing-like-this") == []
    assert len(store.search_history("u1", "e", limit=2)) == 2  # limit respected


# --- archive endpoints -----------------------------------------------------------


class _ArchiveConversation:
    """Just enough ConversationService for the archive routes."""

    def __init__(self, store):
        self.memory = SimpleNamespace(store=store)


def _archive_client(tmp_path, auth_token=None):
    # The API scopes user ids per platform (`api:<id>`, see api.py `_scoped`),
    # so the store must hold the data under the scoped id.
    return TestClient(
        create_app(
            _ArchiveConversation(_seeded_store(tmp_path, user="api:u1")), auth_token=auth_token
        )
    )


def test_sessions_endpoint_returns_the_archive(tmp_path):
    client = _archive_client(tmp_path)

    body = client.get("/v1/sessions?user_id=u1").json()

    ids = [s["id"] for s in body["sessions"]]
    assert ids == ["s2", "s1"]


def test_transcript_endpoint_returns_turns_and_clean_summary(tmp_path):
    client = _archive_client(tmp_path)

    body = client.get("/v1/sessions/s2/transcript?user_id=u1").json()

    assert body["turns"][0]["content"].startswith("remind me")
    assert body["summary"] == "Talked about airing schedules."

    empty = client.get("/v1/sessions/does-not-exist/transcript?user_id=u1").json()
    assert empty == {"turns": [], "summary": None}


def test_search_endpoint_returns_hits(tmp_path):
    client = _archive_client(tmp_path)

    body = client.get("/v1/sessions/search?q=docker&user_id=u1").json()

    assert body["hits"] and body["hits"][0]["session_id"] == "s1"


def test_archive_endpoints_require_bearer_token_when_configured(tmp_path):
    client = _archive_client(tmp_path, auth_token="secret")
    assert client.get("/v1/sessions?user_id=u1").status_code == 401
    assert client.get("/v1/sessions/search?q=x&user_id=u1").status_code == 401
    assert client.get("/v1/sessions/s1/transcript?user_id=u1").status_code == 401


# --- context budgets + pressure fold ----------------------------------------------


def test_section_budget_clamps_the_rendered_section(tmp_path):
    mgr = MemoryManager(store=FileMemoryStore(tmp_path / "memory"), short_term_max=5)
    mgr.set_scope(user_id="u1", session_id="s1")
    mgr.record_episode("x" * 500)

    old = config.context_section_budgets
    configure(context_section_budgets={"episodes": 120})
    try:
        context = mgr.build_context()
    finally:
        configure(context_section_budgets=old)

    assert "…[truncated" in context
    # And without a budget nothing truncates (historic behavior).
    assert "…[truncated" not in mgr.build_context()


def test_context_stats_reports_token_source_and_budgets(tmp_path):
    mgr = MemoryManager(store=FileMemoryStore(tmp_path / "memory"), short_term_max=5)
    mgr.set_scope(user_id="u1", session_id="s1")

    stats = mgr.context_stats()

    # Default provider in tests is litellm — no tokenizer to ask, so estimate.
    assert stats["token_source"] == "estimate"
    assert stats["warn_ratio"] == config.ctx_warn_ratio
    assert "section_budgets" in stats


async def test_pressure_forces_an_early_fold(tmp_path):
    folded: list[str] = []

    async def summarizer(payload: str) -> str:
        folded.append(payload)
        return "a compact summary"

    mgr = MemoryManager(
        store=FileMemoryStore(tmp_path / "memory"),
        short_term_max=1,
        summarize_session_fn=summarizer,
        summarize_every=50,  # turn-count trigger far away
    )
    mgr.set_scope(user_id="u1", session_id="s1")
    for i in range(4):  # short window → 3 turns land in pending
        mgr.record_user_turn(f"a rather long turn {i} " + "words " * 40)

    old = (config.session_fold_pressure_ratio, config.lead_num_ctx)
    configure(session_fold_pressure_ratio=0.5, lead_num_ctx=100)  # tiny window
    try:
        summary = await mgr.maybe_summarize_session()
    finally:
        configure(session_fold_pressure_ratio=old[0], lead_num_ctx=old[1])

    assert summary == "a compact summary" and folded

    # With pressure off (default), the same state does NOT fold early.
    mgr.set_scope(user_id="u1", session_id="s2")
    for i in range(4):
        mgr.record_user_turn(f"another long turn {i} " + "words " * 40)
    assert await mgr.maybe_summarize_session() is None


# --- llama tokenize counter ---------------------------------------------------------


def test_count_tokens_is_none_off_llamacpp_and_counts_on_it(monkeypatch):
    from magi.core import tokens as tokens_mod

    assert tokens_mod.count_tokens("hello") is None  # provider=litellm in tests

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"tokens": [1, 2, 3]}

    calls: list[str] = []

    def fake_post(url, **kwargs):
        calls.append(url)
        return _Resp()

    monkeypatch.setattr(tokens_mod.httpx, "post", fake_post)
    old = config.model_provider
    configure(model_provider="llamacpp")
    try:
        tokens_mod._cache.clear()
        assert tokens_mod.count_tokens("hello") == 3
        assert tokens_mod.count_tokens("hello") == 3  # cached — one HTTP call
        assert tokens_mod.count_tokens("") == 0
    finally:
        configure(model_provider=old)
        tokens_mod._cache.clear()

    assert len(calls) == 1
    assert calls[0].endswith("/tokenize") and "/v1/" not in calls[0]
