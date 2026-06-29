"""Admin HTTP API — the operator-only management surface (FastAPI).

A SEPARATE deployable from the chat API (`channels/api.py`): it can read and
manage every user's memory and reorganize the knowledge corpus, so its
write-capable surface is deliberately kept off the public brain. It runs as its
own process over the same `data/` tree + Qdrant, builds its own stores (no team,
no model — it never runs the agent), and is reached only through the Next.js BFF
(`web/`), which holds the bearer token server-side. See ADR 0002.

v1 surface (slice 1 — read-only knowledge listing):

    GET  /healthz                       liveness probe (no auth)
    GET  /admin/v1/knowledge/documents  every document in the corpus, derived

Auth: when `ADMIN_AUTH_TOKEN` is set, every `/admin` route requires
`Authorization: Bearer <token>`. The BFF is the only caller, so there are no CORS
headers and no browser-facing token — keep the port unpublished (compose network
only) and let Next.js front it.

Two factories, mirroring `channels/api.py`:

  - `create_admin_app(knowledge, auth_token)` — pure, fully injected (what tests use)
  - `build_admin_app()` — composition root wiring the real stores from config
"""

from typing import Optional

from agno.utils.log import log_info
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from magi.core.knowledge import DocumentSummary, KnowledgeStore


# --- wire format (the public contract; version it, don't break it) -----------
class DocumentSummaryOut(BaseModel):
    """One document row in the admin list — aggregated from its chunks."""

    doc_id: str = Field(description="Stable identity (the ingest path/key).")
    source: str = Field(description="Where the document came from (e.g. filename).")
    scope: str = Field(description="Origin partition; 'global' for the shared corpus.")
    chunk_count: int = Field(description="How many chunks this document is stored as.")
    latest_ts: str = Field(description="Newest chunk timestamp (last ingest).")

    @classmethod
    def of(cls, d: DocumentSummary) -> "DocumentSummaryOut":
        return cls(
            doc_id=d.doc_id,
            source=d.source,
            scope=d.scope,
            chunk_count=d.chunk_count,
            latest_ts=d.latest_ts,
        )


class DocumentList(BaseModel):
    documents: list[DocumentSummaryOut]


def create_admin_app(
    knowledge: KnowledgeStore,
    auth_token: Optional[str] = None,
) -> FastAPI:
    """The FastAPI admin app over an already-built `KnowledgeStore` (pure factory).

    No CORS: the only caller is the server-side BFF, never a browser directly.
    """
    app = FastAPI(title="magi-admin", version="1")

    bearer = HTTPBearer(auto_error=False)

    def require_auth(
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
    ) -> None:
        if auth_token is None:
            return
        if credentials is None or credentials.credentials != auth_token:
            raise HTTPException(status_code=401, detail="invalid or missing bearer token")

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get(
        "/admin/v1/knowledge/documents",
        response_model=DocumentList,
        dependencies=[Depends(require_auth)],
    )
    def list_documents() -> DocumentList:
        return DocumentList(
            documents=[DocumentSummaryOut.of(d) for d in knowledge.list_documents()]
        )

    return app


def build_admin_app() -> FastAPI:
    """Composition root: the real stores from config, served over HTTP.

    The knowledge store is built unconditionally (admin manages the corpus
    regardless of whether the chat-time `search_knowledge` tool is enabled — the
    same reasoning as `scripts/ingest_knowledge.py`)."""
    from magi.core.config import config

    log_info("building admin app")
    if config.admin_auth_token is None:
        log_info("admin: auth DISABLED (ADMIN_AUTH_TOKEN not set) — keep the port unpublished")
    return create_admin_app(KnowledgeStore(), auth_token=config.admin_auth_token)
