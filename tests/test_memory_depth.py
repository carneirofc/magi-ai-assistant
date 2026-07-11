"""Tests for the memory/knowledge depth passes: fact consolidation, the
recall-preview lens, raw-file git history (absent-repo degrade), and the
chat-derived knowledge save tool."""

import pytest
from fastapi.testclient import TestClient

from magi.core.memory import manager as manager_mod
from magi.core.memory.admin import MemoryAdmin
from magi.core.memory.curation import CurationResult, FactOp
from magi.core.memory.manager import MemoryManager
from magi.core.memory.store import FileMemoryStore


@pytest.fixture(autouse=True)
def _reset_scope():
    token = manager_mod._scope.set(None)
    yield
    manager_mod._scope.reset(token)


def _manager(tmp_path, curate_fn=None) -> MemoryManager:
    mgr = MemoryManager(
        store=FileMemoryStore(tmp_path / "memory"),
        short_term_max=3,
        curate_fn=curate_fn,
    )
    mgr.set_scope(user_id="u1", session_id="s1")
    return mgr


# --- consolidation ---------------------------------------------------------------


async def test_consolidate_merges_duplicates_via_the_curator(tmp_path):
    seen_inputs = []

    async def curate(inp):
        seen_inputs.append(inp)
        facts = {line.split("] ", 1)[0][1:]: line for line in inp.current_facts.splitlines()}
        ids = list(facts)
        # Merge the two coffee facts: update the first, delete the second.
        return CurationResult(
            operations=(
                FactOp(op="update", fact_id=ids[0], text="Prefers dark-roast coffee."),
                FactOp(op="delete", fact_id=ids[1]),
            ),
            episode="should be ignored",  # maintenance never logs episodes
            persona_adjustment="should be ignored",
        )

    mgr = _manager(tmp_path, curate_fn=curate)
    mgr.mem.long_term_facts.add("Likes coffee.")
    mgr.mem.long_term_facts.add("Prefers dark roast.")

    applied = await mgr.consolidate_facts()

    assert applied == ["profile"]  # no episode/persona from a maintenance pass
    texts = mgr.mem.long_term_facts.texts()
    assert texts == ["Prefers dark-roast coffee."]
    assert "(maintenance pass" in seen_inputs[0].user_message


async def test_consolidate_noops_without_curator_or_facts(tmp_path):
    assert await _manager(tmp_path).consolidate_facts() is None  # curation off

    async def curate(inp):  # pragma: no cover - must not be called
        raise AssertionError("curator called with an empty sheet")

    assert await _manager(tmp_path, curate_fn=curate).consolidate_facts() is None


# --- recall preview ------------------------------------------------------------


def test_recall_preview_returns_the_section_bodies(tmp_path):
    mgr = _manager(tmp_path)
    mgr.mem.long_term_facts.add("Runs Windows 11.")
    mgr.record_episode("Set up the dev machine together.")

    sections = mgr.recall_preview("windows")

    assert "Runs Windows 11." in sections["long_term"]
    assert "Set up the dev machine" in sections["episodes"]
    assert set(sections) == {"persona", "long_term", "episodes", "short_term"}


# --- git history (degrade path) ----------------------------------------------------


def test_file_history_is_empty_without_a_repo(tmp_path):
    store = FileMemoryStore(tmp_path / "memory")
    store.scoped("u1", "_admin").long_term.append("a fact")
    admin = MemoryAdmin(store)

    assert admin.file_history("raw_long_term", user_id="u1") == []
    assert admin.file_at_version("raw_long_term", "abc123", user_id="u1") is None


# --- admin endpoints ----------------------------------------------------------------


def test_consolidate_and_preview_endpoints(tmp_path):
    from magi.channels.admin import create_admin_app
    from magi.core.knowledge import SubjectRegistry

    async def curate(inp):
        return CurationResult()

    mgr = MemoryManager(
        store=FileMemoryStore(tmp_path / "memory"), short_term_max=3, curate_fn=curate
    )
    mgr.set_scope(user_id="u1", session_id="s1")
    mgr.mem.long_term_facts.add("Owns a Seanime library.")

    class _NoKnowledge:
        def list_documents(self):
            return []

    app = create_admin_app(
        _NoKnowledge(),
        mgr.store,
        SubjectRegistry(tmp_path / "subjects.json"),
        memory_manager=mgr,
    )
    client = TestClient(app)

    body = client.post("/admin/v1/memory/users/u1/consolidate").json()
    assert body["action"] == "consolidate" and body["changed"] is False

    body = client.get("/admin/v1/memory/users/u1/recall-preview?q=seanime").json()
    assert "Owns a Seanime library." in body["sections"]["long_term"]

    body = client.get("/admin/v1/memory/files/persona/history").json()
    assert body == {"kind": "persona", "entries": []}  # versioning off → empty, not error

    resp = client.get("/admin/v1/memory/files/persona/history/abc123")
    assert resp.status_code == 404


# --- save_knowledge tool ---------------------------------------------------------------


def test_save_knowledge_indexes_a_document():
    from magi.agent.tools.knowledge import build_knowledge_tools

    class _FakeStore:
        def __init__(self):
            self.saved = []

        def search(self, query, top_k, *, subject=None, tags=(), scopes=("global",)):
            return []

        def index_document(self, doc_id, text, **kwargs):
            self.saved.append((doc_id, text, kwargs))
            return 3

    store = _FakeStore()
    tools = build_knowledge_tools(store, None, store)
    save = next(t for t in tools if getattr(t, "name", "") == "save_knowledge")

    out = save.entrypoint(
        text="How to rebuild the llama-server cache: stop, wipe, restart.",
        title="llama-server cache rebuild",
        subject="ops",
        tags=["llama", ""],
    )

    assert out.success and out.data.chunks == 3
    doc_id, text, kwargs = store.saved[0]
    assert doc_id.startswith("chat-") and kwargs["source"] == "chat"
    assert kwargs["tags"] == ["llama"]


def test_save_knowledge_reports_a_failed_ingest():
    from magi.agent.tools.knowledge import build_knowledge_tools

    class _DownStore:
        def search(self, query, top_k, *, subject=None, tags=(), scopes=("global",)):
            return []

        def index_document(self, doc_id, text, **kwargs):
            return 0

    tools = build_knowledge_tools(_DownStore(), None, _DownStore())
    save = next(t for t in tools if getattr(t, "name", "") == "save_knowledge")

    out = save.entrypoint(
        text="Some reference material that is long enough to save.", title="t"
    )

    assert not out.success
