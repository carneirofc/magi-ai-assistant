"""Tests for the admin channel (channels.admin) and the knowledge listing it
exposes (core/knowledge.store.list_documents).

`create_admin_app` is a pure factory over an injected `KnowledgeStore`, so the
endpoint tests run against a fake store — no Qdrant. The store-aggregation tests
fake the Qdrant `scroll` so the grouping/sort/pagination logic is pinned without a
live backend (same degradation philosophy as test_knowledge.py).
"""

from fastapi.testclient import TestClient

from magi.channels.admin import create_admin_app
from magi.core.knowledge import DocumentSummary, KnowledgeStore


# --- store: list_documents aggregation --------------------------------------
class _FakePoint:
    def __init__(self, payload):
        self.payload = payload


class _FakeClient:
    """A Qdrant stand-in whose `scroll` returns the given pages then stops."""

    def __init__(self, pages):
        # pages: list of (points, next_offset); the last next_offset must be None.
        self._pages = pages
        self._i = 0

    def scroll(self, **_kwargs):
        page = self._pages[self._i]
        self._i += 1
        return page


def _store_with_client(client):
    store = KnowledgeStore(collection="t")
    store._connect_existing = lambda: client  # type: ignore[method-assign]
    return store


def test_list_documents_groups_chunks_by_doc_id():
    points = [
        _FakePoint({"doc_id": "a.md", "source": "a.md", "scope": "global", "ts": "2026-01-01T00:00:00"}),
        _FakePoint({"doc_id": "a.md", "source": "a.md", "scope": "global", "ts": "2026-01-02T00:00:00"}),
        _FakePoint({"doc_id": "b.md", "source": "b.md", "scope": "global", "ts": "2026-01-03T00:00:00"}),
    ]
    store = _store_with_client(_FakeClient([(points, None)]))

    docs = store.list_documents()

    by_id = {d.doc_id: d for d in docs}
    assert by_id["a.md"] == DocumentSummary(
        doc_id="a.md", source="a.md", scope="global", chunk_count=2,
        latest_ts="2026-01-02T00:00:00",
    )
    assert by_id["b.md"].chunk_count == 1


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


# --- admin app: endpoint + auth ---------------------------------------------
class _FakeKnowledge:
    def __init__(self, documents):
        self._documents = documents

    def list_documents(self):
        return self._documents


def _client(documents=(), auth_token=None):
    app = create_admin_app(_FakeKnowledge(list(documents)), auth_token=auth_token)
    return TestClient(app)


def test_healthz_is_open():
    client = _client(auth_token="secret")
    assert client.get("/healthz").json() == {"status": "ok"}


def test_list_documents_endpoint_returns_rows():
    docs = [
        DocumentSummary(doc_id="a.md", source="a.md", scope="global", chunk_count=2, latest_ts="t2"),
        DocumentSummary(doc_id="b.md", source="b.md", scope="global", chunk_count=1, latest_ts="t1"),
    ]
    resp = _client(documents=docs).get("/admin/v1/knowledge/documents")

    assert resp.status_code == 200
    assert resp.json() == {
        "documents": [
            {"doc_id": "a.md", "source": "a.md", "scope": "global", "chunk_count": 2, "latest_ts": "t2"},
            {"doc_id": "b.md", "source": "b.md", "scope": "global", "chunk_count": 1, "latest_ts": "t1"},
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
