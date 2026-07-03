"""Tests for the item archive (core/items) and its wiring into the item kinds.

The archive pairs object-store bytes with a Qdrant vector. Qdrant + embeddings are
optional deps, so — like the knowledge-store tests — these exercise the blob side
end-to-end against a real `LocalStore` and pin the vector side at its degradation
boundary (no embed / no client => no-op). The kind wiring (knowledge cascade +
reindex, memory snapshot) is verified with a recording fake archive so it needs no
backend at all.
"""

import dataclasses
from pathlib import Path

import magi.core.items.archive as arch
from magi.core.config import Config
from magi.core.items import GLOBAL_SCOPE, ItemArchive, build_item_archive_from_config
from magi.core.storage import LocalStore, StorageError


def _cfg(**overrides):
    return dataclasses.replace(Config(), **overrides)


# --- blob side (real LocalStore) --------------------------------------------
def test_persist_and_read_blob_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setattr(arch, "embed_text", lambda *a, **k: None)  # vector side no-op
    a = ItemArchive(LocalStore(tmp_path), Config(), collection="t")
    assert a.persist("knowledge", "docs/guide.md", data=b"hello", text="Guide") is True
    assert a.read_bytes("knowledge", "docs/guide.md") == b"hello"
    a.remove("knowledge", "docs/guide.md")
    assert a.read_bytes("knowledge", "docs/guide.md") is None


def test_persist_data_only_skips_embedding(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(arch, "embed_text", lambda *a, **k: calls.append(a) or [0.1])
    a = ItemArchive(LocalStore(tmp_path), Config(), collection="t")
    assert a.persist("memory", "u1", data=b"[]") is True  # no text => no vector
    assert a.read_bytes("memory", "u1") == b"[]"
    assert calls == []  # never embedded


def test_persist_returns_false_when_blob_write_fails():
    class _Boom:
        def put_bytes(self, *a, **k):
            raise StorageError("boom")

    a = ItemArchive(_Boom(), Config(), collection="t")
    assert a.persist("knowledge", "d", data=b"x") is False


def test_read_bytes_absent_is_none(tmp_path):
    a = ItemArchive(LocalStore(tmp_path), Config(), collection="t")
    assert a.read_bytes("knowledge", "nope") is None


def test_remove_is_idempotent(tmp_path):
    a = ItemArchive(LocalStore(tmp_path), Config(), collection="t")
    a.remove("knowledge", "never-stored")  # no blob, no client — must not raise


# --- vector side degradation ------------------------------------------------
def test_search_empty_query_returns_empty(tmp_path):
    a = ItemArchive(LocalStore(tmp_path), Config(), collection="t")
    assert a.search("   ", top_k=5) == []


def test_search_no_embedding_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(arch, "embed_text", lambda *a, **k: None)
    a = ItemArchive(LocalStore(tmp_path), Config(), collection="t")
    assert a.search("a real query", top_k=5) == []


def test_persist_text_without_qdrant_still_stores_blob(monkeypatch, tmp_path):
    # Embedding succeeds but qdrant-client is absent -> _ensure_client returns None
    # -> the vector is skipped, yet the blob (source of truth) is still written.
    monkeypatch.setattr(arch, "embed_text", lambda *a, **k: [0.1, 0.2, 0.3])
    a = ItemArchive(LocalStore(tmp_path), Config(), collection="t")
    assert a.persist("knowledge", "d", data=b"body", text="title") is True
    assert a.read_bytes("knowledge", "d") == b"body"


# --- keys / ids -------------------------------------------------------------
def test_key_format():
    a = ItemArchive(LocalStore("x"), Config(), collection="t")
    assert a._key("knowledge", "docs/guide.md", "global") == "items/knowledge/global/docs/guide.md"


def test_point_id_is_deterministic_and_scoped():
    a = ItemArchive(LocalStore("x"), Config(), collection="t")
    assert a._point_id("k", "i", "s") == a._point_id("k", "i", "s")
    assert a._point_id("k", "i", "s") != a._point_id("k", "i", "s2")
    assert a._point_id("k", "i", "s") != a._point_id("k", "j", "s")


def test_to_hit_maps_payload():
    class _P:
        payload = {
            "kind": "file",
            "item_id": "abc",
            "scope": "user:u1",
            "text": "a cat photo",
            "key": "items/file/user:u1/abc",
            "metadata": {"filename": "cat.png"},
        }
        score = 0.91

    hit = ItemArchive._to_hit(_P())
    assert hit.kind == "file" and hit.item_id == "abc" and hit.score == 0.91
    assert hit.metadata == {"filename": "cat.png"}


# --- factory gating ---------------------------------------------------------
def test_build_archive_off_returns_none():
    assert build_item_archive_from_config(_cfg(items_archive_enabled=False)) is None


def test_build_archive_on_returns_archive(monkeypatch, tmp_path):
    cfg = _cfg(items_archive_enabled=True, storage_backend="local")
    store = LocalStore(tmp_path)
    monkeypatch.setattr(arch, "build_object_store", lambda config, backend: store)
    a = build_item_archive_from_config(cfg)
    assert isinstance(a, ItemArchive) and a.store is store


def test_build_archive_unbuildable_store_returns_none(monkeypatch):
    cfg = _cfg(items_archive_enabled=True, storage_backend="s3")
    monkeypatch.setattr(arch, "build_object_store", lambda config, backend: None)  # e.g. boto3 absent
    assert build_item_archive_from_config(cfg) is None


# --- a recording fake the kind-wiring tests share ---------------------------
class FakeArchive:
    """Records persist/remove and serves read_bytes — the wiring contract, no backend."""

    def __init__(self):
        self.persisted: list[tuple] = []
        self.removed: list[tuple] = []
        self._bytes: dict[tuple, bytes] = {}

    def persist(self, kind, item_id, *, scope=GLOBAL_SCOPE, data=None, text=None,
                content_type=None, metadata=None):
        self.persisted.append((kind, item_id, scope, data, text, metadata))
        if data is not None:
            self._bytes[(kind, item_id, scope)] = data
        return True

    def read_bytes(self, kind, item_id, *, scope=GLOBAL_SCOPE):
        return self._bytes.get((kind, item_id, scope))

    def remove(self, kind, item_id, *, scope=GLOBAL_SCOPE):
        self.removed.append((kind, item_id, scope))

    def search(self, query, top_k, *, kinds=(), scopes=()):
        return []


# --- knowledge wiring -------------------------------------------------------
def _kstore(**kw):
    from magi.core.knowledge import KnowledgeStore

    return KnowledgeStore(Config(), collection="t", **kw)


def test_knowledge_archive_original_persists_source_and_doc_vector():
    fake = FakeArchive()
    ks = _kstore(archive=fake)
    ks._archive_original("guide.md", "the body", source="guide.md", title="Guide",
                         subject="ops", tags=["a", "b"])
    assert len(fake.persisted) == 1
    kind, item_id, _scope, data, text, meta = fake.persisted[0]
    assert kind == "knowledge" and item_id == "guide.md" and data == b"the body"
    assert "Guide" in text and "ops" in text  # doc-level index text
    assert meta == {"source": "guide.md", "title": "Guide"}


def test_knowledge_archive_none_is_noop():
    _kstore()._archive_original("d", "t", source="s", title="t", subject="", tags=[])  # no crash


def test_knowledge_delete_cascades_to_archive(monkeypatch):
    fake = FakeArchive()
    ks = _kstore(archive=fake)

    class _Client:
        def delete(self, **k):
            pass

    monkeypatch.setattr(ks, "_connect_existing", lambda: _Client())
    monkeypatch.setattr(ks, "_point_ids_for_doc", lambda client, doc_id: ["p1", "p2"])
    assert ks.delete_document("guide.md") is True
    assert fake.removed == [("knowledge", "guide.md", GLOBAL_SCOPE)]


def test_knowledge_reindex_from_archived_original(monkeypatch):
    fake = FakeArchive()
    fake._bytes[("knowledge", "guide.md", GLOBAL_SCOPE)] = b"original text"
    ks = _kstore(archive=fake)
    monkeypatch.setattr(ks, "get_document", lambda doc_id: None)
    seen = {}

    def _spy(doc_id, text, *, source, title, subject, tags):
        seen.update(doc_id=doc_id, text=text, source=source, title=title, subject=subject, tags=tags)
        return 3

    monkeypatch.setattr(ks, "index_document", _spy)
    assert ks.reindex_document("guide.md") == 3
    assert seen["doc_id"] == "guide.md" and seen["text"] == "original text"


def test_knowledge_reindex_no_original_returns_zero():
    assert _kstore(archive=FakeArchive()).reindex_document("missing") == 0


def test_knowledge_reindex_no_archive_returns_zero():
    assert _kstore().reindex_document("x") == 0


# --- memory wiring ----------------------------------------------------------
def _manager(tmp_path, archive):
    from magi.core.memory import MemoryManager
    from magi.core.memory.store import FileMemoryStore

    return MemoryManager(FileMemoryStore(Path(tmp_path)), Config(), short_term_max=10, archive=archive)


def test_memory_snapshot_persists_fact_sheet(tmp_path):
    from magi.core.memory.adapters import slug

    fake = FakeArchive()
    m = _manager(tmp_path, fake)
    m.set_scope("user A", "s1")
    m.mem.long_term_facts.add("likes tea")
    m._snapshot_facts(m.mem)
    assert len(fake.persisted) == 1
    kind, item_id, _scope, data, text, meta = fake.persisted[0]
    assert kind == "memory" and item_id == slug("user A") and text is None
    assert b"likes tea" in data and meta["user_id"] == "user A"


def test_memory_snapshot_noop_without_archive(tmp_path):
    m = _manager(tmp_path, None)
    m.set_scope("u1", "s1")
    m._snapshot_facts(m.mem)  # no crash, nothing to assert beyond "doesn't raise"


def test_memory_snapshot_missing_file_writes_empty(tmp_path):
    fake = FakeArchive()
    m = _manager(tmp_path, fake)
    m.set_scope("u1", "s1")
    m._snapshot_facts(m.mem)  # no facts written yet
    _kind, _id, _scope, data, _text, _meta = fake.persisted[0]
    assert data == b"[]"
