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
from magi.core.knowledge import DocumentChunk, DocumentDetail, DocumentSummary, KnowledgeStore
from magi.core.memory.store import FileMemoryStore


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


# --- admin app: endpoint + auth ---------------------------------------------
class _FakeKnowledge:
    def __init__(self, documents, detail=None):
        self._documents = documents
        self._detail = detail

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


def _client(documents=(), auth_token=None, memory=None, detail=None):
    if memory is None:
        memory = FileMemoryStore(Path(tempfile.mkdtemp()))  # empty: no users on disk
    app = create_admin_app(
        _FakeKnowledge(list(documents), detail=detail), memory, auth_token=auth_token
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


def test_memory_requires_bearer_when_token_set(tmp_path):
    client = _client(auth_token="secret", memory=_seed_memory(tmp_path))
    assert client.get("/admin/v1/memory/users").status_code == 401
    ok = client.get("/admin/v1/memory/users", headers={"Authorization": "Bearer secret"})
    assert ok.status_code == 200
