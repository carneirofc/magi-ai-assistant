"""The knowledge store — a Qdrant-backed RAG corpus the agent retrieves from.

This is the *knowledge* sibling of semantic *memory* (`magi/core/memory/semantic`),
and deliberately separate from it:

  - memory   : per-user, conversation-derived, mutated by the curator.
  - knowledge: global, document-derived, ingested faithfully and read-only at chat
               time. Chunks are stored verbatim (no LLM extraction) so retrieval
               returns source text, not a paraphrase.

It lives in its own Qdrant collection (`config.knowledge_collection`) but reuses
the shared proxy embedder and the same Qdrant endpoint. Like the memory retriever,
every public method is crash-proof: a missing `qdrant-client`, an unreachable
Qdrant, or a failed embedding logs a warning and degrades to a no-op / empty
result. Knowledge must never break a chat.

Documents carry a `scope` payload field — `"global"` for the shared corpus. The
`scopes=` argument on `search` is the seam for narrowing to per-user/session
origin later (ingest those chunks under e.g. `"user:42"`, then pass that scope);
nothing in the agent uses it yet, but the store already supports it end to end.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Protocol, Sequence

from agno.utils.log import log_info, log_warning

from magi.core.config import config
from magi.core.embeddings import embed_text
from magi.core.knowledge.chunking import chunk_text

GLOBAL_SCOPE = "global"


@dataclass(frozen=True)
class KnowledgeHit:
    """One retrieved chunk: the verbatim text plus where it came from."""

    text: str
    source: str
    score: float
    doc_id: str
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DocumentSummary:
    """One document, aggregated from its chunks for the admin document list.

    A *document* is the set of chunks sharing a `doc_id`; there is no document
    record on disk, so this is derived by scrolling the corpus (see
    `KnowledgeStore.list_documents`). `latest_ts` is the newest chunk timestamp
    (when the doc was last ingested)."""

    doc_id: str
    source: str
    scope: str
    chunk_count: int
    latest_ts: str


class KnowledgeSearcher(Protocol):
    """What the search tool needs from a knowledge backend — keeps the tool layer
    decoupled from Qdrant so it can be faked in tests."""

    def search(
        self, query: str, top_k: int, *, scopes: Sequence[str] = (GLOBAL_SCOPE,)
    ) -> list[KnowledgeHit]: ...


class KnowledgeStore:
    """Qdrant-backed knowledge corpus. All public methods are crash-proof by design."""

    def __init__(
        self,
        collection: Optional[str] = None,
        *,
        chunk_chars: Optional[int] = None,
        chunk_overlap: Optional[int] = None,
    ):
        self.collection = collection or config.knowledge_collection
        self.chunk_chars = chunk_chars if chunk_chars is not None else config.knowledge_chunk_chars
        self.chunk_overlap = (
            chunk_overlap if chunk_overlap is not None else config.knowledge_chunk_overlap
        )
        self._client = None  # lazily built; None means "unavailable, no-op"
        self._dim: Optional[int] = None

    # --- qdrant client (lazy, shared shape with SemanticIndex) --------------
    def _ensure_client(self, dim: int):
        """Build the client + collection on first successful embed. Returns it or None."""
        if self._client is not None:
            return self._client
        try:
            from qdrant_client import QdrantClient, models

            client = QdrantClient(url=config.qdrant_url, api_key=config.qdrant_api_key)
            if not client.collection_exists(self.collection):
                client.create_collection(
                    collection_name=self.collection,
                    vectors_config=models.VectorParams(
                        size=dim, distance=models.Distance.COSINE
                    ),
                )
                log_info(f"knowledge: created Qdrant collection '{self.collection}' (dim={dim})")
            self._client = client
            self._dim = dim
            return client
        except Exception as exc:  # noqa: BLE001
            log_warning(f"knowledge: Qdrant unavailable ({type(exc).__name__}: {exc})")
            return None

    def _connect_existing(self):
        """A client bound to the collection, *without* creating it — for admin
        reads (list/delete) on a cold process where no embed has run yet.

        Unlike `_ensure_client`, this never creates the collection: if it doesn't
        exist there is simply nothing to list, so return None. Caches the client
        on success so later calls reuse it. All failures degrade to None."""
        if self._client is not None:
            return self._client
        try:
            from qdrant_client import QdrantClient

            client = QdrantClient(url=config.qdrant_url, api_key=config.qdrant_api_key)
            if not client.collection_exists(self.collection):
                return None
            self._client = client
            return client
        except Exception as exc:  # noqa: BLE001
            log_warning(f"knowledge: Qdrant unavailable ({type(exc).__name__}: {exc})")
            return None

    # --- ingest -------------------------------------------------------------
    def index_document(
        self,
        doc_id: str,
        text: str,
        *,
        source: str,
        scope: str = GLOBAL_SCOPE,
        metadata: Optional[dict[str, str]] = None,
    ) -> int:
        """Chunk, embed, and upsert one document. Re-ingesting the same `doc_id`
        replaces its previous chunks (so edits and shrinks are clean). Returns the
        number of chunks indexed (0 on empty input or any failure)."""
        chunks = chunk_text(text, size=self.chunk_chars, overlap=self.chunk_overlap)
        if not chunks:
            return 0
        vectors = [embed_text(c) for c in chunks]
        embedded = [(c, v) for c, v in zip(chunks, vectors) if v is not None]
        if not embedded:
            return 0
        client = self._ensure_client(len(embedded[0][1]))
        if client is None:
            return 0
        try:
            from qdrant_client import models

            # Replace-on-reingest: drop any prior chunks for this doc first.
            self._delete_doc(client, doc_id)
            ts = datetime.now(timezone.utc).isoformat()
            points = [
                models.PointStruct(
                    id=uuid.uuid4().hex,
                    vector=vector,
                    payload={
                        "doc_id": doc_id,
                        "source": source,
                        "scope": scope,
                        "chunk_index": i,
                        "text": chunk,
                        "metadata": metadata or {},
                        "ts": ts,
                    },
                )
                for i, (chunk, vector) in enumerate(embedded)
            ]
            client.upsert(collection_name=self.collection, points=points)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"knowledge: upsert failed for doc {doc_id!r} ({type(exc).__name__}: {exc})")
            return 0
        log_info(f"knowledge: indexed {len(points)} chunk(s) for doc {doc_id!r} (source={source})")
        return len(points)

    def delete_document(self, doc_id: str) -> None:
        """Remove all chunks for `doc_id`. No-op when the backend is unavailable.

        Connects to the existing collection if the client is cold (admin deletes
        run in a process that never embedded), so a fresh admin service can delete."""
        client = self._connect_existing()
        if client is None:
            return
        self._delete_doc(client, doc_id)

    # --- enumerate (admin) --------------------------------------------------
    def list_documents(self) -> list[DocumentSummary]:
        """Every document in the corpus, one row per `doc_id`, aggregated from its
        chunks by scrolling Qdrant payloads (no vectors). [] when the collection
        is absent or the backend is unavailable.

        There is no document record to read — the chunks are the source of truth —
        so the list can never drift from what's actually retrievable. Cheap at the
        hand-curated scale this corpus lives at; paginates so it stays correct if it
        grows. Newest-ingested first (by latest chunk ts)."""
        client = self._connect_existing()
        if client is None:
            return []
        try:
            docs: dict[str, dict] = {}
            offset = None
            while True:
                points, offset = client.scroll(
                    collection_name=self.collection,
                    with_payload=True,
                    with_vectors=False,
                    limit=256,
                    offset=offset,
                )
                for p in points:
                    payload = p.payload or {}
                    doc_id = str(payload.get("doc_id", ""))
                    if not doc_id:
                        continue
                    ts = str(payload.get("ts", ""))
                    agg = docs.get(doc_id)
                    if agg is None:
                        docs[doc_id] = {
                            "source": str(payload.get("source", "")),
                            "scope": str(payload.get("scope", GLOBAL_SCOPE)),
                            "chunk_count": 1,
                            "latest_ts": ts,
                        }
                    else:
                        agg["chunk_count"] += 1
                        if ts > agg["latest_ts"]:
                            agg["latest_ts"] = ts
                if offset is None:
                    break
        except Exception as exc:  # noqa: BLE001
            log_warning(f"knowledge: list failed ({type(exc).__name__}: {exc})")
            return []
        summaries = [
            DocumentSummary(
                doc_id=doc_id,
                source=agg["source"],
                scope=agg["scope"],
                chunk_count=agg["chunk_count"],
                latest_ts=agg["latest_ts"],
            )
            for doc_id, agg in docs.items()
        ]
        summaries.sort(key=lambda d: d.latest_ts, reverse=True)
        return summaries

    def _delete_doc(self, client, doc_id: str) -> None:
        try:
            from qdrant_client import models

            client.delete(
                collection_name=self.collection,
                points_selector=models.FilterSelector(
                    filter=models.Filter(
                        must=[
                            models.FieldCondition(
                                key="doc_id", match=models.MatchValue(value=doc_id)
                            )
                        ]
                    )
                ),
            )
        except Exception as exc:  # noqa: BLE001
            log_warning(f"knowledge: delete failed for doc {doc_id!r} ({type(exc).__name__}: {exc})")

    # --- retrieve -----------------------------------------------------------
    def search(
        self, query: str, top_k: int, *, scopes: Sequence[str] = (GLOBAL_SCOPE,)
    ) -> list[KnowledgeHit]:
        """Return up to `top_k` chunks most relevant to `query`, restricted to
        `scopes`. [] on empty query or any failure.

        `scopes` is the per-origin hook: it defaults to the global corpus; pass a
        wider set (e.g. `("global", "user:42")`) once per-user knowledge is ingested.
        """
        if not query.strip():
            return []
        vector = embed_text(query)
        if vector is None:
            return []
        client = self._ensure_client(len(vector))
        if client is None:
            return []
        try:
            from qdrant_client import models

            flt = models.Filter(
                must=[models.FieldCondition(key="scope", match=models.MatchAny(any=list(scopes)))]
            )
            hits = client.query_points(
                collection_name=self.collection,
                query=vector,
                query_filter=flt,
                limit=top_k,
                with_payload=True,
            ).points
        except Exception as exc:  # noqa: BLE001
            log_warning(f"knowledge: search failed ({type(exc).__name__}: {exc})")
            return []
        return [self._to_hit(h) for h in hits if h.payload and h.payload.get("text")]

    @staticmethod
    def _to_hit(point) -> KnowledgeHit:
        payload = point.payload or {}
        meta = payload.get("metadata")
        return KnowledgeHit(
            text=str(payload.get("text", "")),
            source=str(payload.get("source", "")),
            score=float(getattr(point, "score", 0.0) or 0.0),
            doc_id=str(payload.get("doc_id", "")),
            metadata=meta if isinstance(meta, dict) else {},
        )


def build_knowledge_from_config() -> Optional[KnowledgeStore]:
    """Construct the store when enabled in config, else None (feature off)."""
    if not config.knowledge_enabled:
        return None
    log_info(
        f"knowledge: ENABLED (qdrant={config.qdrant_url}, collection={config.knowledge_collection}, "
        f"embed={config.embedding_model_id}, top_k={config.knowledge_top_k})"
    )
    return KnowledgeStore()
