"""The knowledge store — a Qdrant-backed RAG corpus the agent retrieves from.

This is the *knowledge* sibling of semantic *memory* (`core/memory/semantic`),
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

from core.config import config
from core.embeddings import embed_text
from core.knowledge.chunking import chunk_text

GLOBAL_SCOPE = "global"


@dataclass(frozen=True)
class KnowledgeHit:
    """One retrieved chunk: the verbatim text plus where it came from."""

    text: str
    source: str
    score: float
    doc_id: str
    metadata: dict[str, str] = field(default_factory=dict)


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
        """Remove all chunks for `doc_id`. No-op when the backend is unavailable."""
        client = self._client
        if client is None:
            return
        self._delete_doc(client, doc_id)

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
