"""Tests for the admin channel (channels.admin) and the knowledge listing it
exposes (core/knowledge.store.list_documents).

`create_admin_app` is a pure factory over an injected `KnowledgeStore`, so the
endpoint tests run against a fake store — no Qdrant. The store-aggregation tests
fake the Qdrant `scroll` so the grouping/sort/pagination logic is pinned without a
live backend (same degradation philosophy as test_knowledge.py).
"""

import dataclasses
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from magi.channels.admin import create_admin_app
from magi.core.knowledge import (
    DocumentChunk,
    DocumentDetail,
    DocumentSummary,
    KnowledgeStore,
    SubjectRegistry,
)
from magi.core.memory import CurationInput, CurationResult, FactOp, build_memory
from magi.core.memory.store import FileMemoryStore
from magi.core.settings import OperatorSettingsStore


# --- store: list_documents aggregation --------------------------------------
class _FakePoint:
    def __init__(self, payload, id="pid"):
        self.payload = payload
        self.id = id


class _FakeClient:
    """A Qdrant stand-in whose `scroll` returns the given pages then stops, and
    which records `set_payload`/`delete` so writes can be asserted."""

    def __init__(self, pages):
        # pages: list of (points, next_offset); the last next_offset must be None.
        self._pages = pages
        self._i = 0
        self.set_payload_calls: list = []
        self.delete_calls: list = []

    def scroll(self, **_kwargs):
        page = self._pages[self._i]
        self._i += 1
        return page

    def set_payload(self, *, collection_name, payload, points):
        self.set_payload_calls.append((payload, points))

    def delete(self, *, collection_name, points_selector):
        self.delete_calls.append(points_selector)


def _store_with_client(client):
    store = KnowledgeStore(collection="t")
    store._connect_existing = lambda: client  # type: ignore[method-assign]
    return store


def test_list_documents_groups_chunks_by_doc_id():
    points = [
        _FakePoint({"doc_id": "a.md", "source": "a.md", "title": "A", "subject": "Infra",
                    "tags": ["x"], "scope": "global", "ts": "2026-01-01T00:00:00"}),
        _FakePoint({"doc_id": "a.md", "source": "a.md", "title": "A", "subject": "Infra",
                    "tags": ["x"], "scope": "global", "ts": "2026-01-02T00:00:00"}),
        _FakePoint({"doc_id": "b.md", "source": "b.md", "scope": "global", "ts": "2026-01-03T00:00:00"}),
    ]
    store = _store_with_client(_FakeClient([(points, None)]))

    docs = store.list_documents()

    by_id = {d.doc_id: d for d in docs}
    assert by_id["a.md"] == DocumentSummary(
        doc_id="a.md", source="a.md", title="A", subject="Infra", tags=["x"],
        scope="global", chunk_count=2, latest_ts="2026-01-02T00:00:00",
    )
    assert by_id["b.md"].chunk_count == 1


def test_list_documents_title_defaults_to_source():
    # A pre-schema chunk (no title) renders with title == source (backfill).
    points = [_FakePoint({"doc_id": "old.md", "source": "old.md", "ts": "1"})]
    [doc] = _store_with_client(_FakeClient([(points, None)])).list_documents()
    assert doc.title == "old.md" and doc.subject == "" and doc.tags == []


def test_list_documents_sorted_newest_first():
    points = [
        _FakePoint({"doc_id": "old", "ts": "2026-01-01T00:00:00"}),
        _FakePoint({"doc_id": "new", "ts": "2026-02-01T00:00:00"}),
    ]
    docs = _store_with_client(_FakeClient([(points, None)])).list_documents()
    assert [d.doc_id for d in docs] == ["new", "old"]


def test_list_documents_paginates_until_offset_none():
    page1 = ([_FakePoint({"doc_id": "a", "ts": "1"})], "cursor")
    page2 = ([_FakePoint({"doc_id": "b", "ts": "2"})], None)
    docs = _store_with_client(_FakeClient([page1, page2])).list_documents()
    assert {d.doc_id for d in docs} == {"a", "b"}


def test_list_documents_skips_points_without_doc_id():
    points = [_FakePoint({"ts": "1"}), _FakePoint({"doc_id": "ok", "ts": "2"})]
    docs = _store_with_client(_FakeClient([(points, None)])).list_documents()
    assert [d.doc_id for d in docs] == ["ok"]


def test_list_documents_no_collection_returns_empty():
    # _connect_existing returns None when the collection is absent / backend down.
    store = KnowledgeStore(collection="t")
    store._connect_existing = lambda: None  # type: ignore[method-assign]
    assert store.list_documents() == []


def test_get_document_orders_chunks_and_reads_fields():
    points = [
        _FakePoint({"doc_id": "a.md", "source": "a.md", "title": "A", "subject": "Infra",
                    "tags": ["x", "y"], "scope": "global", "chunk_index": 1, "text": "second"}),
        _FakePoint({"doc_id": "a.md", "source": "a.md", "title": "A", "subject": "Infra",
                    "tags": ["x", "y"], "scope": "global", "chunk_index": 0, "text": "first"}),
    ]
    detail = _store_with_client(_FakeClient([(points, None)])).get_document("a.md")
    assert detail is not None
    assert detail.title == "A" and detail.subject == "Infra" and detail.tags == ["x", "y"]
    assert [c.text for c in detail.chunks] == ["first", "second"]


def test_get_document_absent_returns_none():
    # No points match => None (the scroll yields an empty page).
    detail = _store_with_client(_FakeClient([([], None)])).get_document("missing")
    assert detail is None


def test_rename_document_sets_title_over_doc_points():
    points = [
        _FakePoint({"doc_id": "a.md"}, id="p1"),
        _FakePoint({"doc_id": "a.md"}, id="p2"),
        _FakePoint({"doc_id": "other"}, id="p3"),
    ]
    client = _FakeClient([(points, None)])
    ok = _store_with_client(client).rename_document("a.md", "New Title")
    assert ok is True
    # Only this doc's points, payload-only update (identity untouched).
    assert client.set_payload_calls == [({"title": "New Title"}, ["p1", "p2"])]


def test_rename_document_absent_returns_false():
    client = _FakeClient([([], None)])
    assert _store_with_client(client).rename_document("missing", "x") is False
    assert client.set_payload_calls == []


def test_delete_document_removes_doc_points():
    points = [
        _FakePoint({"doc_id": "a.md"}, id="p1"),
        _FakePoint({"doc_id": "other"}, id="p2"),
    ]
    client = _FakeClient([(points, None)])
    assert _store_with_client(client).delete_document("a.md") is True
    assert client.delete_calls == [["p1"]]


def test_delete_document_absent_returns_false():
    client = _FakeClient([([], None)])
    assert _store_with_client(client).delete_document("missing") is False
    assert client.delete_calls == []


def test_tag_document_adds_and_removes_order_preserving():
    points = [
        _FakePoint({"doc_id": "a.md", "tags": ["keep", "drop"]}, id="p1"),
        _FakePoint({"doc_id": "a.md", "tags": ["keep", "drop"]}, id="p2"),
    ]
    client = _FakeClient([(points, None)])
    result = _store_with_client(client).tag_document("a.md", add=["new"], remove=["drop"])
    assert result == ["keep", "new"]
    assert client.set_payload_calls == [({"tags": ["keep", "new"]}, ["p1", "p2"])]


def test_tag_document_dedupes_existing():
    points = [_FakePoint({"doc_id": "a.md", "tags": ["x"]}, id="p1")]
    result = _store_with_client(_FakeClient([(points, None)])).tag_document("a.md", add=["x", "y"])
    assert result == ["x", "y"]


def test_tag_document_absent_returns_none():
    client = _FakeClient([([], None)])
    assert _store_with_client(client).tag_document("missing", add=["x"]) is None
    assert client.set_payload_calls == []


def test_list_tags_unions_distinct_sorted():
    points = [
        _FakePoint({"tags": ["b", "a"]}),
        _FakePoint({"tags": ["a", "c"]}),
        _FakePoint({"tags": []}),
    ]
    assert _store_with_client(_FakeClient([(points, None)])).list_tags() == ["a", "b", "c"]


def test_edit_document_tags_endpoint():
    detail = DocumentDetail(
        doc_id="a.md", source="a.md", title="A", subject="", tags=["keep", "drop"],
        scope="global", chunks=[DocumentChunk(chunk_index=0, text="x")],
    )
    resp = _client(detail=detail).patch(
        "/admin/v1/knowledge/documents/a.md/tags", json={"add": ["new"], "remove": ["drop"]}
    )
    assert resp.status_code == 200
    assert resp.json()["tags"] == ["keep", "new"]


def test_edit_tags_missing_doc_is_404():
    assert _client(detail=None).patch(
        "/admin/v1/knowledge/documents/nope/tags", json={"add": ["x"]}
    ).status_code == 404


def test_list_tags_endpoint():
    assert _client(tags=["docker", "k8s"]).get("/admin/v1/knowledge/tags").json() == {
        "tags": ["docker", "k8s"]
    }


def test_ingest_derives_doc_id_from_title():
    fake = _FakeKnowledge([])
    app = create_admin_app(fake, FileMemoryStore(Path(tempfile.mkdtemp())), SubjectRegistry(Path(tempfile.mkdtemp()) / "s.json"))
    resp = TestClient(app).post(
        "/admin/v1/knowledge/documents",
        json={"title": "My Great Doc!", "text": "hello world"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"doc_id": "my-great-doc", "chunks_indexed": 3}
    assert fake.index_calls[0][0] == "my-great-doc"
    assert fake.index_calls[0][3] == "My Great Doc!"  # title forwarded


def test_ingest_rejects_unknown_subject(tmp_path):
    reg = SubjectRegistry(tmp_path / "s.json")
    resp = _client(subjects=reg).post(
        "/admin/v1/knowledge/documents",
        json={"title": "X", "text": "y", "subject": "Ghost"},
    )
    assert resp.status_code == 422


def test_ingest_with_known_subject_and_tags(tmp_path):
    reg = SubjectRegistry(tmp_path / "s.json")
    reg.create("Infra")
    fake = _FakeKnowledge([])
    app = create_admin_app(fake, FileMemoryStore(tmp_path / "m"), reg)
    resp = TestClient(app).post(
        "/admin/v1/knowledge/documents",
        json={"title": "Doc", "text": "z", "subject": "Infra", "tags": ["docker"]},
    )
    assert resp.status_code == 200
    assert fake.index_calls[0][4] == "Infra" and fake.index_calls[0][5] == ["docker"]


# --- admin app: endpoint + auth ---------------------------------------------
class _FakeKnowledge:
    def __init__(self, documents, detail=None, tags=()):
        self._documents = documents
        self._detail = detail
        self._tags = list(tags)
        self.rename_subject_calls: list = []
        self.index_calls: list = []

    def list_documents(self):
        return self._documents

    def get_document(self, doc_id):
        return self._detail if (self._detail and self._detail.doc_id == doc_id) else None

    def rename_document(self, doc_id, title):
        if self._detail and self._detail.doc_id == doc_id:
            self._detail = dataclasses.replace(self._detail, title=title)
            return True
        return False

    def delete_document(self, doc_id):
        if self._detail and self._detail.doc_id == doc_id:
            self._detail = None
            return True
        return False

    def set_document_subject(self, doc_id, subject):
        if self._detail and self._detail.doc_id == doc_id:
            self._detail = dataclasses.replace(self._detail, subject=subject)
            return True
        return False

    def rename_subject(self, old, new):
        self.rename_subject_calls.append((old, new))
        return 0

    def tag_document(self, doc_id, *, add=(), remove=()):
        if self._detail and self._detail.doc_id == doc_id:
            tags = list(self._detail.tags)
            for t in add:
                if t not in tags:
                    tags.append(t)
            tags = [t for t in tags if t not in set(remove)]
            self._detail = dataclasses.replace(self._detail, tags=tags)
            return tags
        return None

    def list_tags(self):
        return self._tags

    def index_document(self, doc_id, text, *, source, title=None, subject="", tags=None):
        self.index_calls.append((doc_id, text, source, title, subject, list(tags or [])))
        return 3  # canned chunk count


class _FakeRetriever:
    """Records reset/index so the fact-write re-index path can be asserted."""

    def __init__(self):
        self.reset_calls: list = []
        self.index_calls: list = []

    def index(self, user_id, kind, text):
        self.index_calls.append((user_id, kind, text))

    def search(self, user_id, query, kind, top_k):
        return []

    def reset(self, user_id, kind):
        self.reset_calls.append((user_id, kind))


def _client(
    documents=(), auth_token=None, memory=None, detail=None, retriever=None, subjects=None,
    tags=(), settings_store=None,
):
    if memory is None:
        memory = FileMemoryStore(Path(tempfile.mkdtemp()))  # empty: no users on disk
    if subjects is None:
        subjects = SubjectRegistry(Path(tempfile.mkdtemp()) / "subjects.json")
    app = create_admin_app(
        _FakeKnowledge(list(documents), detail=detail, tags=tags),
        memory,
        subjects,
        retriever=retriever,
        auth_token=auth_token,
        settings_store=settings_store,
    )
    return TestClient(app)


def test_healthz_is_open():
    client = _client(auth_token="secret")
    assert client.get("/healthz").json() == {"status": "ok"}


def test_list_documents_endpoint_returns_rows():
    docs = [
        DocumentSummary(doc_id="a.md", source="a.md", title="A", subject="Infra",
                        tags=["x"], scope="global", chunk_count=2, latest_ts="t2"),
    ]
    resp = _client(documents=docs).get("/admin/v1/knowledge/documents")

    assert resp.status_code == 200
    assert resp.json() == {
        "documents": [
            {"doc_id": "a.md", "source": "a.md", "title": "A", "subject": "Infra",
             "tags": ["x"], "scope": "global", "chunk_count": 2, "latest_ts": "t2"},
        ]
    }


def test_list_documents_requires_bearer_when_token_set():
    client = _client(auth_token="secret")

    assert client.get("/admin/v1/knowledge/documents").status_code == 401
    assert (
        client.get(
            "/admin/v1/knowledge/documents",
            headers={"Authorization": "Bearer wrong"},
        ).status_code
        == 401
    )
    ok = client.get(
        "/admin/v1/knowledge/documents", headers={"Authorization": "Bearer secret"}
    )
    assert ok.status_code == 200


def test_no_token_means_open():
    # auth_token None => the gate is a no-op (keep the port unpublished instead).
    assert _client(auth_token=None).get("/admin/v1/knowledge/documents").status_code == 200


def test_get_document_endpoint_returns_chunks():
    detail = DocumentDetail(
        doc_id="a.md", source="a.md", title="A", subject="Infra", tags=["x"],
        scope="global",
        chunks=[DocumentChunk(chunk_index=0, text="first"), DocumentChunk(chunk_index=1, text="second")],
    )
    resp = _client(detail=detail).get("/admin/v1/knowledge/documents/a.md")
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "A" and body["subject"] == "Infra"
    assert [c["text"] for c in body["chunks"]] == ["first", "second"]


def test_get_document_missing_is_404():
    assert _client(detail=None).get("/admin/v1/knowledge/documents/nope.md").status_code == 404


def _detail(doc_id="a.md", title="A"):
    return DocumentDetail(
        doc_id=doc_id, source="a.md", title=title, subject="", tags=[],
        scope="global", chunks=[DocumentChunk(chunk_index=0, text="x")],
    )


def test_rename_document_endpoint_updates_title():
    resp = _client(detail=_detail()).patch(
        "/admin/v1/knowledge/documents/a.md", json={"title": "Renamed"}
    )
    assert resp.status_code == 200
    assert resp.json()["title"] == "Renamed"


def test_rename_document_missing_is_404():
    resp = _client(detail=None).patch(
        "/admin/v1/knowledge/documents/nope.md", json={"title": "X"}
    )
    assert resp.status_code == 404


def test_rename_document_rejects_empty_title():
    resp = _client(detail=_detail()).patch(
        "/admin/v1/knowledge/documents/a.md", json={"title": ""}
    )
    assert resp.status_code == 422  # min_length=1


def test_delete_document_endpoint():
    client = _client(detail=_detail())
    assert client.delete("/admin/v1/knowledge/documents/a.md").status_code == 204
    # Gone now → second delete is a 404.
    assert client.delete("/admin/v1/knowledge/documents/a.md").status_code == 404


def test_delete_document_missing_is_404():
    assert _client(detail=None).delete("/admin/v1/knowledge/documents/nope.md").status_code == 404


# --- subject registry -------------------------------------------------------
def test_subject_registry_create_list_rename_delete(tmp_path):
    reg = SubjectRegistry(tmp_path / "subjects.json")
    s = reg.create("Infra", "infrastructure")
    assert s is not None and s.name == "Infra"
    assert reg.create("infra") is None  # case-insensitive duplicate rejected
    assert [x.name for x in reg.list()] == ["Infra"]
    renamed = reg.rename(s.id, name="Infrastructure")
    assert renamed.name == "Infrastructure"
    assert reg.delete(s.id) is True
    assert reg.list() == []


def test_subjects_endpoints(tmp_path):
    reg = SubjectRegistry(tmp_path / "subjects.json")
    client = _client(subjects=reg)

    created = client.post("/admin/v1/knowledge/subjects", json={"name": "Infra"})
    assert created.status_code == 200
    sid = created.json()["id"]
    assert client.post("/admin/v1/knowledge/subjects", json={"name": "infra"}).status_code == 409

    listed = client.get("/admin/v1/knowledge/subjects").json()
    assert [s["name"] for s in listed["subjects"]] == ["Infra"]

    edited = client.patch(f"/admin/v1/knowledge/subjects/{sid}", json={"name": "Infrastructure"})
    assert edited.status_code == 200 and edited.json()["name"] == "Infrastructure"

    assert client.delete(f"/admin/v1/knowledge/subjects/{sid}").status_code == 204
    assert client.get("/admin/v1/knowledge/subjects").json()["subjects"] == []


def test_subject_rename_cascades_to_corpus(tmp_path):
    reg = SubjectRegistry(tmp_path / "subjects.json")
    sid = reg.create("Infra").id
    fake = _FakeKnowledge([], detail=None)
    app = create_admin_app(fake, FileMemoryStore(tmp_path / "m"), reg)
    client = TestClient(app)
    client.patch(f"/admin/v1/knowledge/subjects/{sid}", json={"name": "Infrastructure"})
    assert fake.rename_subject_calls == [("Infra", "Infrastructure")]


def test_set_document_subject_requires_known_subject(tmp_path):
    reg = SubjectRegistry(tmp_path / "subjects.json")
    detail = DocumentDetail(
        doc_id="a.md", source="a.md", title="A", subject="", tags=[], scope="global",
        chunks=[DocumentChunk(chunk_index=0, text="x")],
    )
    client = _client(detail=detail, subjects=reg)
    # Unknown subject rejected...
    assert client.put(
        "/admin/v1/knowledge/documents/a.md/subject", json={"subject": "Ghost"}
    ).status_code == 422
    # ...known subject accepted.
    reg.create("Infra")
    ok = client.put("/admin/v1/knowledge/documents/a.md/subject", json={"subject": "Infra"})
    assert ok.status_code == 200 and ok.json()["subject"] == "Infra"
    # Clearing ('') is always allowed.
    assert client.put(
        "/admin/v1/knowledge/documents/a.md/subject", json={"subject": ""}
    ).status_code == 200


# --- memory viewer (read-only) ----------------------------------------------
def _seed_memory(tmp_path):
    """A FileMemoryStore with one user's facts/episodes/session + a persona."""
    store = FileMemoryStore(tmp_path)
    mem = store.scoped("u1", "s1")
    mem.long_term_facts.add("lives in Berlin")
    mem.long_term_facts.add("likes anime")
    mem.long_term.append("raw fact one")
    mem.episodes.append("talked about docker")
    mem.live_turns.append("user", "hi", 20)
    mem.live_turns.append("assistant", "hello", 20)
    mem.session_summary.write("earlier we said hi")
    mem.pending.extend([{"role": "user", "content": "older", "ts": "t"}])
    store.persona.append("be concise")
    return store


def test_list_users_aggregates_counts(tmp_path):
    client = _client(memory=_seed_memory(tmp_path))
    data = client.get("/admin/v1/memory/users").json()
    assert data == {
        "users": [
            {"user_id": "u1", "fact_count": 2, "episode_count": 1, "session_count": 1}
        ]
    }


def test_list_users_empty_when_no_memory(tmp_path):
    client = _client(memory=FileMemoryStore(tmp_path))
    assert client.get("/admin/v1/memory/users").json() == {"users": []}


def test_get_profile_returns_facts_and_episodes(tmp_path):
    client = _client(memory=_seed_memory(tmp_path))
    data = client.get("/admin/v1/memory/users/u1/profile").json()
    assert [f["text"] for f in data["facts"]] == ["lives in Berlin", "likes anime"]
    assert all(f["id"] for f in data["facts"])
    assert data["raw_long_term"] == ["raw fact one"]
    assert data["episodes"] == ["talked about docker"]


def test_list_sessions(tmp_path):
    client = _client(memory=_seed_memory(tmp_path))
    assert client.get("/admin/v1/memory/users/u1/sessions").json() == {"sessions": ["s1"]}


def test_get_session_detail(tmp_path):
    client = _client(memory=_seed_memory(tmp_path))
    data = client.get("/admin/v1/memory/users/u1/sessions/s1").json()
    assert [(t["role"], t["content"]) for t in data["turns"]] == [
        ("user", "hi"),
        ("assistant", "hello"),
    ]
    assert "earlier we said hi" in data["summary"]
    assert data["pending"][0]["content"] == "older"


def test_get_persona(tmp_path):
    client = _client(memory=_seed_memory(tmp_path))
    assert "be concise" in client.get("/admin/v1/memory/persona").json()["text"]


# --- operator-triggered memory passes (summarize / curate / flush) ----------
def _client_with_manager(store, *, summarize_fn=None, curate_fn=None, auth_token=None):
    """An admin client whose trigger endpoints run against a real MemoryManager
    over `store`, with the summarizer/curator faked (or left None to test 503)."""
    manager = build_memory(
        store=store,
        short_term_max=20,
        summarize_session_fn=summarize_fn,
        curate_fn=curate_fn,
    )
    app = create_admin_app(
        _FakeKnowledge([], detail=None),
        store,
        SubjectRegistry(Path(tempfile.mkdtemp()) / "subjects.json"),
        auth_token=auth_token,
        memory_manager=manager,
    )
    return TestClient(app)


def test_trigger_503_without_a_manager(tmp_path):
    # The default admin client wires no manager (standalone-with-no-brain shape).
    client = _client(memory=_seed_memory(tmp_path))
    for action in ("summarize", "curate", "flush"):
        resp = client.post(f"/admin/v1/memory/users/u1/sessions/s1/{action}")
        assert resp.status_code == 503


def test_summarize_trigger_503_when_no_summarizer(tmp_path):
    # A manager is present but model-free (summarize_fn None) → honest 503.
    client = _client_with_manager(_seed_memory(tmp_path))
    resp = client.post("/admin/v1/memory/users/u1/sessions/s1/summarize")
    assert resp.status_code == 503
    assert "no model" in resp.json()["detail"]


def test_summarize_trigger_folds_pending(tmp_path):
    async def fake_summarize(payload: str) -> str:
        return f"SUMMARY[{payload[:12]}]"

    store = _seed_memory(tmp_path)
    client = _client_with_manager(store, summarize_fn=fake_summarize)
    resp = client.post("/admin/v1/memory/users/u1/sessions/s1/summarize")
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "summarize" and body["changed"] is True
    # The rolling summary was rewritten and the pending buffer drained.
    detail = client.get("/admin/v1/memory/users/u1/sessions/s1").json()
    assert "SUMMARY" in detail["summary"]
    assert detail["pending"] == []


def test_summarize_trigger_noop_when_nothing_pending(tmp_path):
    async def fake_summarize(payload: str) -> str:
        return "unused"

    store = FileMemoryStore(tmp_path)
    store.scoped("u1", "s1").live_turns.append("user", "hi", 20)  # a session, no pending
    client = _client_with_manager(store, summarize_fn=fake_summarize)
    resp = client.post("/admin/v1/memory/users/u1/sessions/s1/summarize")
    assert resp.status_code == 200
    assert resp.json()["changed"] is False


def test_curate_trigger_503_when_no_curator(tmp_path):
    client = _client_with_manager(_seed_memory(tmp_path))
    resp = client.post("/admin/v1/memory/users/u1/sessions/s1/curate")
    assert resp.status_code == 503


def test_curate_trigger_applies_from_session_summary(tmp_path):
    seen: dict = {}

    async def fake_curate(inp: CurationInput) -> CurationResult:
        seen["user_message"] = inp.user_message
        return CurationResult(
            operations=(FactOp(op="add", text="prefers dark mode"),),
            episode="discussed preferences",
        )

    store = _seed_memory(tmp_path)  # session_summary = "earlier we said hi"
    client = _client_with_manager(store, curate_fn=fake_curate)
    resp = client.post("/admin/v1/memory/users/u1/sessions/s1/curate")
    assert resp.status_code == 200
    body = resp.json()
    assert body["changed"] is True and "profile" in body["detail"] and "episode" in body["detail"]
    # The curator was fed the session's rolling summary.
    assert "earlier we said hi" in seen["user_message"]
    # The added fact lands on the user-level profile the fact editor shows.
    profile = client.get("/admin/v1/memory/users/u1/profile").json()
    assert "prefers dark mode" in [f["text"] for f in profile["facts"]]


def test_curate_trigger_noop_when_no_summary(tmp_path):
    async def fake_curate(inp: CurationInput) -> CurationResult:
        raise AssertionError("curator must not run without a summary")

    store = FileMemoryStore(tmp_path)
    store.scoped("u1", "s1").live_turns.append("user", "hi", 20)  # session, empty summary
    client = _client_with_manager(store, curate_fn=fake_curate)
    resp = client.post("/admin/v1/memory/users/u1/sessions/s1/curate")
    assert resp.status_code == 200
    assert resp.json()["changed"] is False


def test_flush_trigger_carries_summary_into_episode(tmp_path):
    # Flush is model-free, so it works with a manager that has no summarizer/curator.
    store = _seed_memory(tmp_path)  # 2 live turns + a rolling summary
    client = _client_with_manager(store)
    resp = client.post("/admin/v1/memory/users/u1/sessions/s1/flush")
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "flush" and body["changed"] is True and "2 live turn" in body["detail"]
    # Live window is wiped and the summary is carried into an episode.
    detail = client.get("/admin/v1/memory/users/u1/sessions/s1").json()
    assert detail["turns"] == []
    profile = client.get("/admin/v1/memory/users/u1/profile").json()
    assert any("earlier we said hi" in e for e in profile["episodes"])


# --- bot identity CRUD + optimistic concurrency -----------------------------
# The base64 of a 1x1 transparent PNG — sent straight as the `data_base64` field.
_PNG_1x1 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M8AAAMBAQDJ/pLvAAAAAElFTkSuQmCC"


def test_get_identity_defaults_to_empty(tmp_path):
    client = _client(memory=FileMemoryStore(tmp_path))
    body = client.get("/admin/v1/identity").json()
    assert body["display_name"] == "" and body["description"] == ""
    assert body["has_avatar"] is False and body["version"]


def test_put_identity_sets_fields_and_moves_version(tmp_path):
    client = _client(memory=FileMemoryStore(tmp_path))
    before = client.get("/admin/v1/identity").json()

    resp = client.put(
        "/admin/v1/identity",
        json={"display_name": "Alyssa", "description": "calm", "expected_version": before["version"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["display_name"] == "Alyssa" and body["description"] == "calm"
    assert body["version"] != before["version"]


def test_put_identity_stale_version_is_409(tmp_path):
    client = _client(memory=FileMemoryStore(tmp_path))
    resp = client.put(
        "/admin/v1/identity",
        json={"display_name": "X", "description": "", "expected_version": "stale"},
    )
    assert resp.status_code == 409


def test_identity_avatar_upload_serve_and_clear(tmp_path):
    client = _client(memory=FileMemoryStore(tmp_path))
    version = client.get("/admin/v1/identity").json()["version"]

    up = client.put(
        "/admin/v1/identity/avatar",
        json={"data_base64": _PNG_1x1, "mime_type": "image/png", "filename": "me.png",
              "expected_version": version},
    )
    assert up.status_code == 200
    body = up.json()
    assert body["has_avatar"] is True and body["avatar_mime"] == "image/png"
    assert body["avatar_filename"] == "me.png"

    served = client.get("/admin/v1/identity/avatar")
    assert served.status_code == 200 and served.headers["content-type"].startswith("image/png")

    cleared = client.delete(f"/admin/v1/identity/avatar?expected_version={body['version']}")
    assert cleared.status_code == 200 and cleared.json()["has_avatar"] is False
    assert client.get("/admin/v1/identity/avatar").status_code == 404


def test_identity_avatar_unsupported_mime_is_422(tmp_path):
    client = _client(memory=FileMemoryStore(tmp_path))
    resp = client.put(
        "/admin/v1/identity/avatar",
        json={"data_base64": _PNG_1x1, "mime_type": "application/pdf"},
    )
    assert resp.status_code == 422


def test_identity_avatar_bad_base64_is_422(tmp_path):
    client = _client(memory=FileMemoryStore(tmp_path))
    resp = client.put(
        "/admin/v1/identity/avatar",
        json={"data_base64": "!!!not-base64!!!", "mime_type": "image/png"},
    )
    assert resp.status_code == 422


def test_identity_requires_bearer_when_token_set(tmp_path):
    client = _client(auth_token="secret", memory=FileMemoryStore(tmp_path))
    assert client.get("/admin/v1/identity").status_code == 401
    ok = client.get("/admin/v1/identity", headers={"Authorization": "Bearer secret"})
    assert ok.status_code == 200


def test_memory_requires_bearer_when_token_set(tmp_path):
    client = _client(auth_token="secret", memory=_seed_memory(tmp_path))
    assert client.get("/admin/v1/memory/users").status_code == 401
    ok = client.get("/admin/v1/memory/users", headers={"Authorization": "Bearer secret"})
    assert ok.status_code == 200


# --- fact CRUD + optimistic concurrency + semantic reset --------------------
def _profile(client, user="u1"):
    return client.get(f"/admin/v1/memory/users/{user}/profile").json()


def test_add_fact_appends_and_returns_new_version(tmp_path):
    store = _seed_memory(tmp_path)
    retriever = _FakeRetriever()
    client = _client(memory=store, retriever=retriever)
    before = _profile(client)

    resp = client.post("/admin/v1/memory/users/u1/facts", json={"text": "new fact"})
    assert resp.status_code == 200
    body = resp.json()
    assert [f["text"] for f in body["facts"]][-1] == "new fact"
    assert body["version"] != before["version"]  # version moved
    # Re-index rebuilt the whole long_term slice from current facts.
    assert retriever.reset_calls == [("u1", "long_term")]
    assert ("u1", "long_term", "new fact") in retriever.index_calls


def test_update_fact(tmp_path):
    store = _seed_memory(tmp_path)
    client = _client(memory=store)
    fact_id = _profile(client)["facts"][0]["id"]

    resp = client.patch(
        f"/admin/v1/memory/users/u1/facts/{fact_id}", json={"text": "moved to Munich"}
    )
    assert resp.status_code == 200
    assert any(f["text"] == "moved to Munich" for f in resp.json()["facts"])


def test_update_missing_fact_is_404(tmp_path):
    client = _client(memory=_seed_memory(tmp_path))
    assert (
        client.patch("/admin/v1/memory/users/u1/facts/nope", json={"text": "x"}).status_code == 404
    )


def test_delete_fact(tmp_path):
    store = _seed_memory(tmp_path)
    client = _client(memory=store)
    fact_id = _profile(client)["facts"][0]["id"]

    resp = client.delete(f"/admin/v1/memory/users/u1/facts/{fact_id}")
    assert resp.status_code == 200
    assert fact_id not in [f["id"] for f in resp.json()["facts"]]


def test_stale_version_is_409(tmp_path):
    client = _client(memory=_seed_memory(tmp_path))
    resp = client.post(
        "/admin/v1/memory/users/u1/facts",
        json={"text": "x", "expected_version": "deadbeef"},
    )
    assert resp.status_code == 409


def test_current_version_is_accepted(tmp_path):
    client = _client(memory=_seed_memory(tmp_path))
    version = _profile(client)["version"]
    resp = client.post(
        "/admin/v1/memory/users/u1/facts",
        json={"text": "x", "expected_version": version},
    )
    assert resp.status_code == 200


# --- raw-file editor --------------------------------------------------------
def test_get_and_put_persona_raw(tmp_path):
    client = _client(memory=_seed_memory(tmp_path))
    got = client.get("/admin/v1/memory/files/persona").json()
    assert "be concise" in got["content"]

    resp = client.put(
        "/admin/v1/memory/files/persona",
        json={"content": "# Persona\n\nbe terse\n", "expected_version": got["version"]},
    )
    assert resp.status_code == 200
    assert "be terse" in client.get("/admin/v1/memory/files/persona").json()["content"]


def test_put_raw_stale_version_409(tmp_path):
    client = _client(memory=_seed_memory(tmp_path))
    resp = client.put(
        "/admin/v1/memory/files/persona",
        json={"content": "x", "expected_version": "deadbeef"},
    )
    assert resp.status_code == 409


def test_put_session_window_validates_json(tmp_path):
    client = _client(memory=_seed_memory(tmp_path))
    base = "/admin/v1/memory/files/session_window?user_id=u1&session_id=s1"
    # A valid JSON list is accepted...
    ok = client.put(base, json={"content": '[{"role": "user", "content": "hi"}]'})
    assert ok.status_code == 200
    # ...a non-list and broken JSON are rejected.
    assert client.put(base, json={"content": '{"not": "a list"}'}).status_code == 422
    assert client.put(base, json={"content": "{bad json"}).status_code == 422


def test_episodes_raw_edit_reindexes(tmp_path):
    retriever = _FakeRetriever()
    client = _client(memory=_seed_memory(tmp_path), retriever=retriever)
    body = "# Episodic memory\n\n- 2026-01-01 :: talked about k8s\n"
    resp = client.put(
        "/admin/v1/memory/files/episodes?user_id=u1", json={"content": body}
    )
    assert resp.status_code == 200
    assert retriever.reset_calls == [("u1", "episode")]
    assert ("u1", "episode", "talked about k8s") in retriever.index_calls


def test_unknown_file_kind_404(tmp_path):
    assert _client(memory=_seed_memory(tmp_path)).get(
        "/admin/v1/memory/files/bogus?user_id=u1"
    ).status_code == 404


def test_session_kind_requires_session_id(tmp_path):
    assert _client(memory=_seed_memory(tmp_path)).get(
        "/admin/v1/memory/files/session_window?user_id=u1"
    ).status_code == 422


# --- operator settings: memory location + git-versioning --------------------
def _settings_client(tmp_path, *, memory=None, auth_token=None):
    memory = memory or FileMemoryStore(tmp_path / "mem")
    store = OperatorSettingsStore(tmp_path / "operator-settings.json")
    return _client(memory=memory, settings_store=store, auth_token=auth_token), memory, store


def test_get_memory_settings_reports_defaults_and_active_dir(tmp_path):
    memory = FileMemoryStore(tmp_path / "mem")
    client, _, _ = _settings_client(tmp_path, memory=memory)

    body = client.get("/admin/v1/settings/memory").json()
    assert body["git_enabled"] is False  # config default
    assert body["active_memory_dir"] == str(memory.root)
    assert body["version"]  # a token is always present (hash of the empty file)


def test_put_memory_settings_persists_and_moves_version(tmp_path):
    client, _, store = _settings_client(tmp_path)
    before = client.get("/admin/v1/settings/memory").json()

    resp = client.put(
        "/admin/v1/settings/memory",
        json={
            "memory_dir": "~/magi-mem",
            "git_enabled": True,
            "git_author_name": "op",
            "git_author_email": "op@host",
            "expected_version": before["version"],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["memory_dir"] == "~/magi-mem" and body["git_enabled"] is True
    assert body["git_author_name"] == "op"
    assert body["version"] != before["version"]
    # Persisted to disk, so a fresh read of the store agrees.
    assert store.read_memory().memory_dir == "~/magi-mem"


def test_put_memory_settings_stale_version_is_409(tmp_path):
    client, _, _ = _settings_client(tmp_path)
    resp = client.put(
        "/admin/v1/settings/memory",
        json={"memory_dir": "/data/mem", "git_enabled": False, "expected_version": "stale"},
    )
    assert resp.status_code == 409


def test_memory_settings_restart_required_reflects_drift(tmp_path):
    memory = FileMemoryStore(tmp_path / "mem")
    client, _, _ = _settings_client(tmp_path, memory=memory)

    # Point at the dir the process is already using -> no restart pending.
    aligned = client.put(
        "/admin/v1/settings/memory",
        json={"memory_dir": str(memory.root), "git_enabled": False},
    ).json()
    assert aligned["restart_required"] is False

    # Point somewhere else -> a restart is needed to pick it up.
    moved = client.put(
        "/admin/v1/settings/memory",
        json={"memory_dir": str(tmp_path / "elsewhere"), "git_enabled": False,
              "expected_version": aligned["version"]},
    ).json()
    assert moved["restart_required"] is True


def test_put_memory_settings_503_without_store(tmp_path):
    # No settings store wired (e.g. a minimal app): writes are refused, not faked.
    client = _client(memory=FileMemoryStore(tmp_path / "mem"))
    resp = client.put(
        "/admin/v1/settings/memory",
        json={"memory_dir": "/data/mem", "git_enabled": False},
    )
    assert resp.status_code == 503


def test_memory_settings_requires_bearer_when_token_set(tmp_path):
    client, _, _ = _settings_client(tmp_path, auth_token="secret")
    assert client.get("/admin/v1/settings/memory").status_code == 401
    ok = client.get("/admin/v1/settings/memory", headers={"Authorization": "Bearer secret"})
    assert ok.status_code == 200


# --- expression pack (mood-keyed portraits; issue #26) ------------------------
def test_expression_upload_serve_list_and_delete(tmp_path):
    client = _client(memory=FileMemoryStore(tmp_path))
    version = client.get("/admin/v1/identity").json()["version"]

    up = client.put(
        "/admin/v1/identity/expressions/wry",
        json={"data_base64": _PNG_1x1, "mime_type": "image/png", "filename": "wry.png",
              "expected_version": version},
    )
    assert up.status_code == 200
    body = up.json()
    assert body["expressions"]["wry"]["mime"] == "image/png"
    assert body["expressions"]["wry"]["filename"] == "wry.png"
    assert body["expressions"]["wry"]["version"]
    assert body["version"] != version  # global token moved

    served = client.get("/admin/v1/identity/expressions/wry")
    assert served.status_code == 200 and served.headers["content-type"].startswith("image/png")

    gone = client.delete(
        f"/admin/v1/identity/expressions/wry?expected_version={body['version']}"
    )
    assert gone.status_code == 200 and gone.json()["expressions"] == {}
    assert client.get("/admin/v1/identity/expressions/wry").status_code == 404


def test_neutral_expression_upload_is_the_avatar(tmp_path):
    client = _client(memory=FileMemoryStore(tmp_path))
    version = client.get("/admin/v1/identity").json()["version"]

    up = client.put(
        "/admin/v1/identity/expressions/neutral",
        json={"data_base64": _PNG_1x1, "mime_type": "image/png",
              "expected_version": version},
    )
    assert up.status_code == 200
    body = up.json()
    assert body["has_avatar"] is True  # neutral IS the avatar slot
    assert "neutral" in body["expressions"]
    assert client.get("/admin/v1/identity/avatar").status_code == 200


def test_expression_bad_mood_key_is_422(tmp_path):
    client = _client(memory=FileMemoryStore(tmp_path))
    resp = client.put(
        "/admin/v1/identity/expressions/Not%20A%20Mood",
        json={"data_base64": _PNG_1x1, "mime_type": "image/png"},
    )
    assert resp.status_code == 422


def test_expression_stale_version_is_409(tmp_path):
    client = _client(memory=FileMemoryStore(tmp_path))
    resp = client.put(
        "/admin/v1/identity/expressions/wry",
        json={"data_base64": _PNG_1x1, "mime_type": "image/png", "expected_version": "stale"},
    )
    assert resp.status_code == 409
