"""The item archive — the "persist original + index" hook for admin-managed items.

A single mechanism shared by the three kinds of item an operator manages —
**knowledge documents**, durable **memory facts**, and **stored files** — so each
gets the same durable shape:

  - the item's *canonical bytes* live in the object store (S3 or local filesystem;
    `config.storage_backend`) — the source of truth, re-indexable;
  - a *searchable vector* lives in a Qdrant collection (`config.items_collection`),
    so items are findable by meaning across kinds.

`persist` ties the two on a write and `remove` drops both on a delete, so the byte
original and the search index never drift. Either side is optional per call — pass
`data` for the blob, `text` for the vector, or both — because the kinds enter from
different sides:

  - knowledge: `data` = the original document text (chunk vectors already live in
               the knowledge collection); `text` = a doc-level summary line.
  - memory   : `data` = the fact sheet JSON snapshot (the semantic mirror already
               holds the per-fact vectors); no `text`.
  - files    : `text` = the filename + note (the bytes already live in the user's
               file archive); no `data`.

This pairs the object-store backend with Qdrant but is gated by its OWN flag
(`config.items_archive_enabled`), independent of `storage_enabled` /
`semantic_memory`. Like the knowledge store and the semantic index, every method is
crash-proof: a missing `qdrant-client`, an unreachable Qdrant, a down object store,
or a failed embedding logs a warning and degrades to a no-op / empty result. Item
archival must never break a chat or an ingest.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Sequence, Union

from agno.utils.log import log_info, log_warning

from magi.core.config import config
from magi.core.embeddings import embed_text
from magi.core.storage import LocalStore, S3Store, StorageError, build_object_store

GLOBAL_SCOPE = "global"

# A fixed namespace so a (kind, scope, item_id) triple always maps to the same
# Qdrant point id — re-persisting an item overwrites its vector in place instead of
# leaving a duplicate, and remove() can target it without a scan.
_POINT_NS = uuid.UUID("a8f5b3c2-1d4e-4f6a-9b0c-7e2d1a3c5f80")

ObjectStore = Union[LocalStore, S3Store]


@dataclass(frozen=True)
class ItemHit:
    """One archived item retrieved by `search` — what it is and where its bytes live."""

    kind: str
    item_id: str
    scope: str
    text: str
    score: float
    key: str = ""
    metadata: dict[str, str] = field(default_factory=dict)


class ItemArchive:
    """Object-store bytes + a Qdrant vector for admin-managed items. Crash-proof."""

    def __init__(self, store: ObjectStore, *, collection: Optional[str] = None):
        self.store = store
        self.collection = collection or config.items_collection
        self._client = None  # lazily built; None means "Qdrant unavailable, no-op"
        self._dim: Optional[int] = None

    # --- keys / ids ---------------------------------------------------------
    def _key(self, kind: str, item_id: str, scope: str) -> str:
        """Object-store key for an item's canonical bytes."""
        return f"items/{kind}/{scope}/{item_id}"

    def _point_id(self, kind: str, item_id: str, scope: str) -> str:
        """Deterministic Qdrant point id for an item (stable across re-persists)."""
        return uuid.uuid5(_POINT_NS, f"{kind}\x00{scope}\x00{item_id}").hex

    # --- qdrant client (lazy, shared shape with SemanticIndex/KnowledgeStore) ---
    def _ensure_client(self, dim: int):
        """Build the client + collection on first successful embed. None on failure."""
        if self._client is not None:
            return self._client
        try:
            from qdrant_client import QdrantClient, models

            client = QdrantClient(url=config.qdrant_url, api_key=config.qdrant_api_key)
            if not client.collection_exists(self.collection):
                client.create_collection(
                    collection_name=self.collection,
                    vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE),
                )
                log_info(f"items: created Qdrant collection '{self.collection}' (dim={dim})")
            self._client = client
            self._dim = dim
            return client
        except Exception as exc:  # noqa: BLE001
            log_warning(f"items: Qdrant unavailable ({type(exc).__name__}: {exc})")
            return None

    def _connect_existing(self):
        """A client bound to the collection *without* creating it — for remove on a
        process that never embedded. None when the collection/backend is absent."""
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
            log_warning(f"items: Qdrant unavailable ({type(exc).__name__}: {exc})")
            return None

    # --- write --------------------------------------------------------------
    def persist(
        self,
        kind: str,
        item_id: str,
        *,
        scope: str = GLOBAL_SCOPE,
        data: Optional[bytes] = None,
        text: Optional[str] = None,
        content_type: Optional[str] = None,
        metadata: Optional[dict[str, str]] = None,
    ) -> bool:
        """Archive an item: store its `data` bytes (when given) and index its `text`
        vector (when given). Returns whether the *byte* write succeeded — that is the
        source of truth; vector indexing is best-effort (a failure is logged, not
        fatal). When `data` is None the blob side is skipped and the return reflects
        only that there was nothing to fail."""
        blob_ok = True
        if data is not None:
            blob_ok = self._put_blob(kind, item_id, scope, data, content_type, metadata)
        if text and text.strip():
            self._index(kind, item_id, scope, text, metadata)
        return blob_ok

    def _put_blob(
        self,
        kind: str,
        item_id: str,
        scope: str,
        data: bytes,
        content_type: Optional[str],
        metadata: Optional[dict[str, str]],
    ) -> bool:
        key = self._key(kind, item_id, scope)
        try:
            self.store.put_bytes(key, data, content_type=content_type, metadata=metadata)
        except StorageError as exc:
            log_warning(f"items: blob put failed for {kind}/{item_id} ({exc})")
            return False
        log_info(f"items: archived {kind}/{item_id} ({len(data)} bytes) under scope {scope!r}")
        return True

    def _index(
        self,
        kind: str,
        item_id: str,
        scope: str,
        text: str,
        metadata: Optional[dict[str, str]],
    ) -> None:
        vector = embed_text(text)
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
                        id=self._point_id(kind, item_id, scope),
                        vector=vector,
                        payload={
                            "kind": kind,
                            "item_id": item_id,
                            "scope": scope,
                            "text": text,
                            "key": self._key(kind, item_id, scope),
                            "metadata": dict(metadata or {}),
                            "ts": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                ],
            )
        except Exception as exc:  # noqa: BLE001
            log_warning(f"items: index failed for {kind}/{item_id} ({type(exc).__name__}: {exc})")

    # --- read ---------------------------------------------------------------
    def read_bytes(self, kind: str, item_id: str, *, scope: str = GLOBAL_SCOPE) -> Optional[bytes]:
        """The item's canonical bytes, or None when absent / the backend is down.
        This is how a kind re-indexes from the source of truth (e.g. knowledge
        re-chunks the original after a chunking-policy change)."""
        key = self._key(kind, item_id, scope)
        try:
            data, _ctype, _meta = self.store.get_bytes(key)
            return data
        except StorageError:
            return None

    # --- delete -------------------------------------------------------------
    def remove(self, kind: str, item_id: str, *, scope: str = GLOBAL_SCOPE) -> None:
        """Drop both the item's bytes and its vector (idempotent; best-effort)."""
        key = self._key(kind, item_id, scope)
        try:
            self.store.delete_bytes(key)
        except StorageError as exc:
            log_warning(f"items: blob delete failed for {kind}/{item_id} ({exc})")
        client = self._connect_existing()
        if client is None:
            return
        try:
            client.delete(
                collection_name=self.collection,
                points_selector=[self._point_id(kind, item_id, scope)],
            )
        except Exception as exc:  # noqa: BLE001
            log_warning(f"items: vector delete failed for {kind}/{item_id} ({type(exc).__name__}: {exc})")

    # --- search -------------------------------------------------------------
    def search(
        self,
        query: str,
        top_k: int,
        *,
        kinds: Sequence[str] = (),
        scopes: Sequence[str] = (),
    ) -> list[ItemHit]:
        """Up to `top_k` archived items most relevant to `query`, optionally filtered
        to `kinds` and/or `scopes` (empty = no filter on that field). [] on empty
        query or any failure."""
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

            must = []
            if kinds:
                must.append(models.FieldCondition(key="kind", match=models.MatchAny(any=list(kinds))))
            if scopes:
                must.append(models.FieldCondition(key="scope", match=models.MatchAny(any=list(scopes))))
            points = client.query_points(
                collection_name=self.collection,
                query=vector,
                query_filter=models.Filter(must=must) if must else None,
                limit=top_k,
                with_payload=True,
            ).points
        except Exception as exc:  # noqa: BLE001
            log_warning(f"items: search failed ({type(exc).__name__}: {exc})")
            return []
        return [self._to_hit(p) for p in points if p.payload]

    @staticmethod
    def _to_hit(point) -> ItemHit:
        payload = point.payload or {}
        meta = payload.get("metadata")
        return ItemHit(
            kind=str(payload.get("kind", "")),
            item_id=str(payload.get("item_id", "")),
            scope=str(payload.get("scope", "")),
            text=str(payload.get("text", "")),
            score=float(getattr(point, "score", 0.0) or 0.0),
            key=str(payload.get("key", "")),
            metadata=meta if isinstance(meta, dict) else {},
        )


def build_item_archive_from_config() -> Optional[ItemArchive]:
    """Construct the archive when `items_archive_enabled`, else None (feature off).

    Builds its own object store (ungated by `storage_enabled` — the archive has its
    own flag) and ensures the bucket/dir exists. Returns None when the object store
    can't be built (e.g. the S3 backend without boto3), so a deployment that turns
    the flag on without the backend ready still boots; the wiring degrades to no
    archival.
    """
    if not config.items_archive_enabled:
        return None
    store = build_object_store(config.storage_backend)
    if store is None:
        log_warning(
            "items: archive enabled but the object store could not be built "
            f"(backend={config.storage_backend!r}) — item archival disabled"
        )
        return None
    try:
        store.ensure_bucket()
    except Exception as exc:  # noqa: BLE001 — a down backend must not abort startup.
        log_info(f"items: backend check skipped ({type(exc).__name__}: {exc})")
    log_info(
        f"items: archive ENABLED (backend={config.storage_backend}, "
        f"qdrant={config.qdrant_url}, collection={config.items_collection})"
    )
    return ItemArchive(store)
