"""Optional semantic memory search over Qdrant.

Gated behind `config.semantic_memory`. Long-term facts and episodes are embedded
(via the LiteLLM proxy) and stored in Qdrant so `build_context` can pull only the
top-k entries relevant to the current message instead of dumping whole files into
the window — the fix for "long history, don't load it all, search a summary".

Everything here degrades gracefully. If `qdrant-client` isn't installed, or Qdrant
or the embedding endpoint is unreachable, `index`/`search` log a warning and become
no-ops and the manager falls back to whole-file injection. Memory must never break
a chat, so no exception from this module is allowed to escape.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional, Protocol

from agno.utils.log import log_info, log_warning

from magi.core.config import config
from magi.core.embeddings import embed_text


class MemoryRetriever(Protocol):
    """What the manager needs from a retriever — keeps core decoupled from Qdrant."""

    def index(self, user_id: str, kind: str, text: str) -> None: ...

    def search(self, user_id: str, query: str, kind: str, top_k: int) -> list[str]: ...

    def reset(self, user_id: str, kind: str) -> None: ...


class SemanticIndex:
    """Qdrant-backed retriever. All public methods are crash-proof by design."""

    def __init__(self, collection: str = "chatbot_memory"):
        self.collection = collection
        self._client = None  # lazily built; None means "unavailable, no-op"
        self._dim: Optional[int] = None

    # --- embedding ----------------------------------------------------------
    def _embed(self, text: str) -> Optional[list[float]]:
        return embed_text(text)  # shared proxy embedder; None on any failure

    # --- qdrant client (lazy) ----------------------------------------------
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
                log_info(f"semantic: created Qdrant collection '{self.collection}' (dim={dim})")
            self._client = client
            self._dim = dim
            return client
        except Exception as exc:  # noqa: BLE001
            log_warning(f"semantic: Qdrant unavailable ({type(exc).__name__}: {exc})")
            return None

    # --- public API ---------------------------------------------------------
    def index(self, user_id: str, kind: str, text: str) -> None:
        """Embed + upsert one memory entry. No-op on any failure."""
        if not text.strip():
            return
        vector = self._embed(text)
        if vector is None:
            return
        client = self._ensure_client(len(vector))
        if client is None:
            return
        try:
            from qdrant_client import models

            client.upsert(
                collection_name=self.collection,
                points=[
                    models.PointStruct(
                        id=uuid.uuid4().hex,
                        vector=vector,
                        payload={
                            "user_id": str(user_id),
                            "kind": kind,
                            "text": text,
                            "ts": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                ],
            )
        except Exception as exc:  # noqa: BLE001
            log_warning(f"semantic: upsert failed ({type(exc).__name__}: {exc})")

    def reset(self, user_id: str, kind: str) -> None:
        """Drop every indexed point for one `(user_id, kind)` slice. No-op when the
        backend is unavailable.

        The mirror is otherwise append-only, so a deleted/edited memory entry would
        linger as a ghost vector; the admin re-index calls this then re-`index`es the
        current entries, making semantic recall reflect the edit. See ADR 0002."""
        client = self._client
        if client is None:
            return
        try:
            from qdrant_client import models

            client.delete(
                collection_name=self.collection,
                points_selector=models.FilterSelector(
                    filter=models.Filter(
                        must=[
                            models.FieldCondition(key="user_id", match=models.MatchValue(value=str(user_id))),
                            models.FieldCondition(key="kind", match=models.MatchValue(value=kind)),
                        ]
                    )
                ),
            )
        except Exception as exc:  # noqa: BLE001
            log_warning(f"semantic: reset failed ({type(exc).__name__}: {exc})")

    def search(self, user_id: str, query: str, kind: str, top_k: int) -> list[str]:
        """Return up to `top_k` stored texts most relevant to `query`. [] on failure."""
        if not query.strip():
            return []
        vector = self._embed(query)
        if vector is None:
            return []
        client = self._ensure_client(len(vector))
        if client is None:
            return []
        try:
            from qdrant_client import models

            flt = models.Filter(
                must=[
                    models.FieldCondition(key="user_id", match=models.MatchValue(value=str(user_id))),
                    models.FieldCondition(key="kind", match=models.MatchValue(value=kind)),
                ]
            )
            hits = client.query_points(
                collection_name=self.collection,
                query=vector,
                query_filter=flt,
                limit=top_k,
            ).points
            return [h.payload["text"] for h in hits if h.payload and h.payload.get("text")]
        except Exception as exc:  # noqa: BLE001
            log_warning(f"semantic: search failed ({type(exc).__name__}: {exc})")
            return []


def build_semantic_index() -> Optional[SemanticIndex]:
    """Construct the index when enabled in config, else None (feature off)."""
    if not config.semantic_memory:
        return None
    log_info(
        f"semantic: ENABLED (qdrant={config.qdrant_url}, embed={config.embedding_model_id}, "
        f"top_k={config.semantic_top_k})"
    )
    return SemanticIndex()
