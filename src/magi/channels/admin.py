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
from magi.core.memory.store import FileMemoryStore

# A session id is irrelevant when reading user-level files (facts, episodes,
# persona): those paths don't depend on it. Use a fixed placeholder so the
# scope bundle resolves without inventing one per request.
_USER_SCOPE_SID = "_admin"


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


# --- memory wire format ------------------------------------------------------
class UserSummary(BaseModel):
    """One user row in the memory viewer."""

    user_id: str
    fact_count: int
    episode_count: int
    session_count: int


class UserList(BaseModel):
    users: list[UserSummary]


class Fact(BaseModel):
    id: str
    text: str
    ts: str = ""


class Profile(BaseModel):
    """A user's durable memory, read-only: curated facts + any raw `remember`
    facts + episode bodies."""

    facts: list[Fact]
    raw_long_term: list[str]
    episodes: list[str]


class SessionList(BaseModel):
    sessions: list[str]


class Turn(BaseModel):
    role: str = ""
    content: str = ""
    ts: str = ""


class SessionDetail(BaseModel):
    """One session's machine-managed state, read-only."""

    turns: list[Turn]
    summary: str
    pending: list[Turn]


class Persona(BaseModel):
    text: str


def create_admin_app(
    knowledge: KnowledgeStore,
    memory: FileMemoryStore,
    auth_token: Optional[str] = None,
) -> FastAPI:
    """The FastAPI admin app over already-built stores (pure factory).

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

    # --- memory (read-only viewer; CRUD arrives in later slices) -----------
    @app.get(
        "/admin/v1/memory/users",
        response_model=UserList,
        dependencies=[Depends(require_auth)],
    )
    def list_users() -> UserList:
        users: list[UserSummary] = []
        for user_id in memory.list_users():
            mem = memory.scoped(user_id, _USER_SCOPE_SID)
            users.append(
                UserSummary(
                    user_id=user_id,
                    fact_count=len(mem.long_term_facts.read()),
                    episode_count=mem.episodes.count(),
                    session_count=len(memory.list_sessions(user_id)),
                )
            )
        return UserList(users=users)

    @app.get(
        "/admin/v1/memory/users/{user_id}/profile",
        response_model=Profile,
        dependencies=[Depends(require_auth)],
    )
    def get_profile(user_id: str) -> Profile:
        mem = memory.scoped(user_id, _USER_SCOPE_SID)
        return Profile(
            facts=[
                Fact(id=str(f.get("id", "")), text=str(f.get("text", "")), ts=str(f.get("ts", "")))
                for f in mem.long_term_facts.read()
            ],
            raw_long_term=mem.long_term.bodies(),
            episodes=mem.episodes.bodies(),
        )

    @app.get(
        "/admin/v1/memory/users/{user_id}/sessions",
        response_model=SessionList,
        dependencies=[Depends(require_auth)],
    )
    def list_user_sessions(user_id: str) -> SessionList:
        return SessionList(sessions=memory.list_sessions(user_id))

    @app.get(
        "/admin/v1/memory/users/{user_id}/sessions/{session_id}",
        response_model=SessionDetail,
        dependencies=[Depends(require_auth)],
    )
    def get_session(user_id: str, session_id: str) -> SessionDetail:
        mem = memory.scoped(user_id, session_id)
        return SessionDetail(
            turns=[Turn(**_turn(t)) for t in mem.live_turns.read()],
            summary=mem.session_summary.read(),
            pending=[Turn(**_turn(t)) for t in mem.pending.read()],
        )

    @app.get(
        "/admin/v1/memory/persona",
        response_model=Persona,
        dependencies=[Depends(require_auth)],
    )
    def get_persona() -> Persona:
        return Persona(text=memory.persona.read())

    return app


def _turn(t: dict) -> dict:
    """A stored turn dict narrowed to the wire fields (tolerates missing keys)."""
    return {
        "role": str(t.get("role", "")),
        "content": str(t.get("content", "")),
        "ts": str(t.get("ts", "")),
    }


def build_admin_app() -> FastAPI:
    """Composition root: the real stores from config, served over HTTP.

    Both stores are built unconditionally (admin manages memory + the corpus
    regardless of whether the chat-time tools are enabled — the same reasoning as
    `scripts/ingest_knowledge.py`)."""
    from pathlib import Path

    from magi.core.config import config

    log_info("building admin app")
    if config.admin_auth_token is None:
        log_info("admin: auth DISABLED (ADMIN_AUTH_TOKEN not set) — keep the port unpublished")
    return create_admin_app(
        KnowledgeStore(),
        FileMemoryStore(Path(config.memory_dir)),
        auth_token=config.admin_auth_token,
    )
