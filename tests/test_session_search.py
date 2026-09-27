"""Tests for session search (core/session_index + agent/tools/session_search)
and the delegate tool (agent/tools/delegate)."""

import asyncio
from types import SimpleNamespace

import pytest

from magi.agent.tools import delegate as delegate_mod
from magi.agent.tools import enabled_tools
from magi.agent.tools.session_search import build_session_search_tools
from magi.core import session_index as si
from magi.core.conversation import ConversationService
from magi.core.memory.manager import MemoryManager
from magi.core.memory.store import FileMemoryStore


def _call(fn, **kwargs):
    assert fn.entrypoint is not None
    return fn.entrypoint(**kwargs)


@pytest.fixture
def index(tmp_path):
    idx = si.SessionIndex(tmp_path / "sessions.db")
    yield idx
    idx.close()


def test_search_is_scoped_to_the_user(index):
    index.add(user_id="u1", session_id="s1", role="user", text="the backup script uses rsync")
    index.add(user_id="u2", session_id="s9", role="user", text="my backup script is secret")
    hits = index.search(user_id="u1", query="backup script", limit=10)
    assert [h.session_id for h in hits] == ["s1"]
    assert "[backup]" in hits[0].snippet
    assert index.search(user_id="u3", query="backup") == []


def test_query_operators_are_neutralised(index):
    index.add(user_id="u1", session_id="s1", role="user", text="plain words here")
    # FTS5 syntax in user text must not raise or change semantics.
    for q in ['"unbalanced', "NEAR(a b)", "plain AND OR NOT", "col:plain", "*"]:
        index.search(user_id="u1", query=q)
    assert si.match_expression("  ***  ") is None
    assert si.match_expression('rsync "backup"') == '"rsync" OR "backup"'


def test_open_degrades_without_fts5(tmp_path, monkeypatch):
    monkeypatch.setattr(si, "fts5_available", lambda: False)
    assert si.open_session_index(tmp_path / "x.db") is None


def test_ranking_prefers_more_matches(index):
    index.add(user_id="u", session_id="a", role="user", text="rsync")
    index.add(user_id="u", session_id="b", role="user", text="rsync backup nightly cron")
    hits = index.search(user_id="u", query="rsync backup cron")
    assert hits[0].session_id == "b"


# --- service indexing -----------------------------------------------------------


class _Runner:
    async def arun(self, **_kwargs):
        return SimpleNamespace(status="COMPLETED", content="use rsync -a", reasoning_content=None)


async def test_service_indexes_user_and_reply(index, tmp_path):
    memory = MemoryManager(store=FileMemoryStore(tmp_path / "memory"), short_term_max=3)
    service = ConversationService(runner=_Runner(), memory=memory, session_index=index)  # pyright: ignore[reportArgumentType]
    await service.handle(user_id="api:1", session_id="s1", text="how do I copy the backup")
    roles = sorted(h.role for h in index.search(user_id="api:1", query="rsync backup"))
    assert roles == ["assistant", "user"]


async def test_index_failure_never_breaks_the_turn(tmp_path):
    class Broken:
        def add(self, **_kwargs):
            raise RuntimeError("disk full")

    memory = MemoryManager(store=FileMemoryStore(tmp_path / "memory"), short_term_max=3)
    service = ConversationService(runner=_Runner(), memory=memory, session_index=Broken())  # pyright: ignore[reportArgumentType]
    reply = await service.handle(user_id="u", session_id="s", text="hi")
    assert reply.text == "use rsync -a" and not reply.is_error


# --- tool -------------------------------------------------------------------------


def test_tool_uses_current_scope_only(index, tmp_path):
    memory = MemoryManager(store=FileMemoryStore(tmp_path / "memory"), short_term_max=3)
    index.add(user_id="discord:1", session_id="old", role="user", text="deploy the gateway")
    index.add(user_id="discord:2", session_id="x", role="user", text="deploy the gateway too")
    (tool,) = build_session_search_tools(index, memory)
    memory.set_scope("discord:1", "now")
    out = _call(tool, query="gateway deploy")
    assert out.success
    assert [h.session_id for h in out.data.hits] == ["old"]
    assert out.data.hits[0].this_session is False


# --- delegate ----------------------------------------------------------------------


class _FakeAgent:
    last: _FakeAgent | None = None
    reply: object = SimpleNamespace(status="COMPLETED", content="done: 42")
    delay: float = 0.0

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        _FakeAgent.last = self

    async def arun(self, *, input):
        self.input = input
        await asyncio.sleep(self.delay)
        return self.reply


@pytest.fixture
def fake_agent(monkeypatch):
    monkeypatch.setattr(delegate_mod, "Agent", _FakeAgent)
    _FakeAgent.reply = SimpleNamespace(status="COMPLETED", content="done: 42")
    _FakeAgent.delay = 0.0
    return _FakeAgent


def _delegate(timeout=5.0):
    (tool,) = delegate_mod.build_delegate_tools(
        lambda: SimpleNamespace(id="m"),  # pyright: ignore[reportArgumentType]
        enabled_tools,
        timeout_seconds=timeout,
        tool_call_limit=3,
    )
    return tool


async def test_delegate_returns_result_and_is_isolated(fake_agent):
    out = await _call(_delegate(), goal="compute the answer please", context="use math")
    assert out.success and out.data.result == "done: 42"
    agent = fake_agent.last
    assert agent is not None
    assert agent.kwargs["tool_call_limit"] == 3
    names = {getattr(t, "name", getattr(t, "__name__", "")) for t in agent.kwargs["tools"]}
    assert "delegate_task" not in names  # depth 1: no recursion
    assert not any(n.startswith(("remember", "forget", "recall")) for n in names)
    assert "use math" in agent.input


async def test_delegate_timeout_is_a_failure(fake_agent):
    fake_agent.delay = 1.0
    out = await _call(_delegate(timeout=0.01), goal="slow task that takes long")
    assert not out.success and "did not finish" in out.message


async def test_delegate_empty_answer_is_a_failure(fake_agent):
    fake_agent.reply = SimpleNamespace(status="COMPLETED", content="")
    out = await _call(_delegate(), goal="produce nothing at all")
    assert not out.success
