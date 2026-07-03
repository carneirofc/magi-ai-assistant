"""Tests for the knowledge core (core/knowledge): chunking, store degradation,
result mapping, and config gating.

The Qdrant-touching paths degrade to no-ops when the backend/embeddings are
unavailable, which is exactly what these pin — no Qdrant or proxy is needed. The
faithful-retrieval contract (chunks stored verbatim) is covered by the chunker
tests; the tool-facing contract lives in test_knowledge_tools.py.
"""

import dataclasses

import magi.core.knowledge.store as store_mod
from magi.core.config import Config
from magi.core.knowledge import KnowledgeStore, build_knowledge_from_config, chunk_text
from magi.core.knowledge.store import KnowledgeHit, blend_by_tags


def _hit(text, score, tags=()):
    return KnowledgeHit(text=text, source="s", score=score, doc_id="d", tags=list(tags))


# --- chunking ---------------------------------------------------------------
def test_chunk_empty_is_no_chunks():
    assert chunk_text("", size=100, overlap=10) == []
    assert chunk_text("   \n\n  ", size=100, overlap=10) == []


def test_chunk_short_text_is_single_chunk():
    assert chunk_text("a short note", size=100, overlap=10) == ["a short note"]


def test_chunk_splits_on_paragraph_boundaries():
    text = "First paragraph here.\n\nSecond paragraph here.\n\nThird one."
    chunks = chunk_text(text, size=30, overlap=0)
    assert len(chunks) >= 2
    # No chunk exceeds the size budget by much, and content is preserved verbatim.
    assert all(len(c) <= 30 for c in chunks)
    assert "First paragraph here." in chunks[0]


def test_chunk_overlap_repeats_tail_between_chunks():
    text = "abcdefghij klmnopqrst uvwxyz0123 456789ABCD"  # no early boundaries
    chunks = chunk_text(text, size=20, overlap=8)
    assert len(chunks) >= 2
    # Adjacent chunks share some text (the overlap) — continuity across the cut.
    assert any(
        chunks[i][-4:] in chunks[i + 1] or chunks[i + 1][:4] in chunks[i]
        for i in range(len(chunks) - 1)
    )


def test_chunk_hard_cuts_unbroken_blob():
    blob = "x" * 250  # no boundaries at all
    chunks = chunk_text(blob, size=100, overlap=10)
    assert len(chunks) >= 3
    assert all(len(c) <= 100 for c in chunks)
    assert "".join(chunks).count("x") >= 250  # nothing lost (overlap repeats some)


def test_chunk_overlap_clamped_below_size_guarantees_progress():
    # overlap >= size would loop forever without the clamp; assert it terminates.
    chunks = chunk_text("y" * 50, size=10, overlap=999)
    assert len(chunks) >= 5
    assert all(c for c in chunks)


# --- store degradation ------------------------------------------------------
def test_index_document_empty_text_indexes_nothing(monkeypatch):
    calls = []
    monkeypatch.setattr(store_mod, "embed_text", lambda *a, **k: calls.append(a) or [0.1])
    store = KnowledgeStore(Config(), collection="t", chunk_chars=100, chunk_overlap=10)

    assert store.index_document("d1", "   ", source="s") == 0
    assert calls == []  # never even embedded


def test_index_document_no_embedding_degrades_to_zero(monkeypatch):
    # Embedding unavailable (proxy down) => no chunks indexed, no crash.
    monkeypatch.setattr(store_mod, "embed_text", lambda *a, **k: None)
    store = KnowledgeStore(Config(), collection="t", chunk_chars=100, chunk_overlap=10)

    assert store.index_document("d1", "real content here", source="s") == 0


def test_search_empty_query_returns_empty(monkeypatch):
    monkeypatch.setattr(store_mod, "embed_text", lambda *a, **k: [0.1, 0.2])
    store = KnowledgeStore(Config(), collection="t")

    assert store.search("   ", top_k=5) == []


def test_search_no_embedding_returns_empty(monkeypatch):
    monkeypatch.setattr(store_mod, "embed_text", lambda *a, **k: None)
    store = KnowledgeStore(Config(), collection="t")

    assert store.search("a real query", top_k=5) == []


# --- result mapping ---------------------------------------------------------
class _FakePoint:
    def __init__(self, payload, score):
        self.payload = payload
        self.score = score


def test_to_hit_maps_payload_and_score():
    point = _FakePoint(
        {"text": "the answer", "source": "guide.md", "doc_id": "guide.md", "metadata": {"k": "v"}},
        0.87,
    )
    hit = KnowledgeStore._to_hit(point)
    assert hit == KnowledgeHit(
        text="the answer", source="guide.md", score=0.87, doc_id="guide.md", metadata={"k": "v"}
    )


def test_to_hit_tolerates_missing_fields():
    hit = KnowledgeStore._to_hit(_FakePoint({"text": "x"}, None))
    assert hit.text == "x" and hit.source == "" and hit.score == 0.0 and hit.metadata == {}


# --- tag soft-boost (blend) -------------------------------------------------
def test_blend_no_query_tags_is_identity():
    hits = [_hit("a", 0.9), _hit("b", 0.8)]
    assert blend_by_tags(hits, [], weight=0.2) == hits


def test_blend_promotes_tag_match_past_higher_vector_score():
    # b starts lower (0.50 vs 0.55) but matches both query tags; with weight 0.2 it
    # gains +0.2 and overtakes a.
    a = _hit("a", 0.55, tags=["x"])
    b = _hit("b", 0.50, tags=["docker", "ci"])
    out = blend_by_tags([a, b], ["docker", "ci"], weight=0.2)
    assert [h.text for h in out] == ["b", "a"]


def test_blend_never_excludes_unmatched():
    a = _hit("a", 0.9, tags=[])
    b = _hit("b", 0.4, tags=["docker"])
    out = blend_by_tags([a, b], ["docker"], weight=0.1)
    # b boosted by 0.1 -> 0.5, still below a's 0.9: a stays first, b retained.
    assert [h.text for h in out] == ["a", "b"]


def test_blend_is_case_insensitive():
    out = blend_by_tags([_hit("a", 0.1, tags=["Docker"])], ["docker"], weight=1.0)
    assert out[0].score == 0.1  # original score unchanged; only ordering uses the blend


# --- config gating ----------------------------------------------------------
def test_build_from_config_off_returns_none():
    config = dataclasses.replace(Config(), knowledge_enabled=False)
    assert build_knowledge_from_config(config) is None


def test_build_from_config_on_returns_store():
    config = dataclasses.replace(Config(), knowledge_enabled=True)
    store = build_knowledge_from_config(config)
    assert isinstance(store, KnowledgeStore)
