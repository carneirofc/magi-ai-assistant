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

import base64
from typing import Optional

from agno.utils.log import log_info
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from magi.core.items import ItemArchive
from magi.core.knowledge import (
    DocumentDetail,
    DocumentSummary,
    KnowledgeStore,
    Subject,
    SubjectRegistry,
)
from magi.core.memory import MemoryManager
from magi.core.memory.adapters import slug as _mem_slug
from magi.core.memory.semantic import MemoryRetriever
from magi.core.memory.store import FileMemoryStore, ScopedMemory

# A session id is irrelevant when reading user-level files (facts, episodes,
# persona): those paths don't depend on it. Use a fixed placeholder so the
# scope bundle resolves without inventing one per request.
_USER_SCOPE_SID = "_admin"
# The retriever kind key for long-term facts (matches LongTerm.retriever_key), so
# an admin fact edit re-indexes the right semantic slice.
_LONG_TERM_KIND = "long_term"


# --- wire format (the public contract; version it, don't break it) -----------
class DocumentSummaryOut(BaseModel):
    """One document row in the admin list — aggregated from its chunks."""

    doc_id: str = Field(description="Stable identity (the ingest path/key).")
    source: str = Field(description="Where the document came from (e.g. filename).")
    title: str = Field(description="Human display label (defaults to source).")
    subject: str = Field(description="Single controlled grouping ('' when unset).")
    tags: list[str] = Field(description="Free-form labels.")
    scope: str = Field(description="Origin partition; 'global' for the shared corpus.")
    chunk_count: int = Field(description="How many chunks this document is stored as.")
    latest_ts: str = Field(description="Newest chunk timestamp (last ingest).")

    @classmethod
    def of(cls, d: DocumentSummary) -> "DocumentSummaryOut":
        return cls(
            doc_id=d.doc_id,
            source=d.source,
            title=d.title,
            subject=d.subject,
            tags=d.tags,
            scope=d.scope,
            chunk_count=d.chunk_count,
            latest_ts=d.latest_ts,
        )


class DocumentList(BaseModel):
    documents: list[DocumentSummaryOut]


class RenameDocument(BaseModel):
    """Edit a document's display label. Identity (`doc_id`) is untouched."""

    title: str = Field(min_length=1, description="The new display title.")


class IngestDocument(BaseModel):
    """Add a document. The resolver (paste / upload) has already produced
    `(title, text)`; re-using an existing `doc_id` replaces that document."""

    title: str = Field(min_length=1, description="Display title (and source).")
    text: str = Field(min_length=1, description="The document's full text.")
    doc_id: Optional[str] = Field(default=None, description="Identity; derived from title if absent.")
    subject: str = Field(default="", description="Subject (must exist in the registry), or ''.")
    tags: list[str] = Field(default_factory=list)


class IngestResult(BaseModel):
    doc_id: str
    chunks_indexed: int


class SetSubject(BaseModel):
    """Assign a document's subject (the controlled grouping). '' clears it."""

    subject: str = Field(description="The subject name (from the registry), or '' to clear.")


class EditTags(BaseModel):
    """Add and/or remove free-form tags on a document."""

    add: list[str] = Field(default_factory=list)
    remove: list[str] = Field(default_factory=list)


class TagList(BaseModel):
    tags: list[str]


# --- subject registry wire format --------------------------------------------
class SubjectOut(BaseModel):
    id: str
    name: str
    description: str = ""

    @classmethod
    def of(cls, s: Subject) -> "SubjectOut":
        return cls(id=s.id, name=s.name, description=s.description)


class SubjectListOut(BaseModel):
    subjects: list[SubjectOut]


class CreateSubject(BaseModel):
    name: str = Field(min_length=1, description="The subject name (unique, case-insensitive).")
    description: str = Field(default="", description="Optional description.")


class EditSubject(BaseModel):
    name: Optional[str] = Field(default=None, description="New name (cascades to tagged docs).")
    description: Optional[str] = Field(default=None)


class ChunkOut(BaseModel):
    chunk_index: int
    text: str


class DocumentDetailOut(BaseModel):
    """A single document: doc-level fields + its chunks in order."""

    doc_id: str
    source: str
    title: str
    subject: str
    tags: list[str]
    scope: str
    chunks: list[ChunkOut]

    @classmethod
    def of(cls, d: DocumentDetail) -> "DocumentDetailOut":
        return cls(
            doc_id=d.doc_id,
            source=d.source,
            title=d.title,
            subject=d.subject,
            tags=d.tags,
            scope=d.scope,
            chunks=[ChunkOut(chunk_index=c.chunk_index, text=c.text) for c in d.chunks],
        )


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
    """A user's durable memory: curated facts + any raw `remember` facts + episode
    bodies. `version` is the facts file's optimistic-concurrency token — echo it on
    a fact write or risk a 409."""

    facts: list[Fact]
    raw_long_term: list[str]
    episodes: list[str]
    version: str


class AddFact(BaseModel):
    text: str = Field(min_length=1, description="The fact to add.")
    expected_version: Optional[str] = Field(
        default=None, description="The version from the last read; rejected with 409 if stale."
    )


class UpdateFact(BaseModel):
    text: str = Field(min_length=1, description="The fact's new text.")
    expected_version: Optional[str] = Field(default=None)


class FactsResult(BaseModel):
    """The facts after a write, plus the new version token to carry forward."""

    facts: list[Fact]
    version: str


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


# --- bot identity wire format ------------------------------------------------
class IdentityOut(BaseModel):
    """The bot's presented identity. `version` is the optimistic-concurrency token
    (over fields + picture bytes) — echo it on a write or risk a 409. The picture
    itself is served/uploaded separately, never inlined here."""

    display_name: str = ""
    description: str = ""
    has_avatar: bool = False
    avatar_mime: Optional[str] = None
    avatar_filename: Optional[str] = None
    version: str = ""


class UpdateIdentity(BaseModel):
    """Set the bot's name + description (the picture is managed on its own route)."""

    display_name: str = Field(default="", description="The bot's display name ('' to clear).")
    description: str = Field(default="", description="Free-form identity notes ('' to clear).")
    expected_version: Optional[str] = Field(
        default=None, description="The version from the last read; rejected with 409 if stale."
    )


class SetAvatar(BaseModel):
    """Upload a new profile picture — base64 bytes (a `data:` URI is accepted too)."""

    data_base64: str = Field(min_length=1, description="The image bytes, base64-encoded.")
    mime_type: str = Field(min_length=1, description="The image mime type (e.g. image/png).")
    filename: Optional[str] = Field(default=None, description="Original filename, for display.")
    expected_version: Optional[str] = Field(default=None)


class RawFile(BaseModel):
    """One memory file's raw content + its version token (optimistic concurrency)."""

    kind: str
    content: str
    version: str


class PutRawFile(BaseModel):
    content: str = Field(description="The full new file content (replaces the file).")
    expected_version: Optional[str] = Field(
        default=None, description="The version from the last read; rejected with 409 if stale."
    )


class MemoryTriggerResult(BaseModel):
    """The outcome of an operator-triggered memory pass (summarize / curate / flush)."""

    action: str = Field(description="Which pass ran: 'summarize', 'curate', or 'flush'.")
    changed: bool = Field(description="Whether the pass actually changed anything.")
    detail: str = Field(description="A human-readable one-line summary of what happened.")


def create_admin_app(
    knowledge: KnowledgeStore,
    memory: FileMemoryStore,
    subjects: SubjectRegistry,
    retriever: Optional[MemoryRetriever] = None,
    auth_token: Optional[str] = None,
    archive: Optional[ItemArchive] = None,
    memory_manager: Optional[MemoryManager] = None,
) -> FastAPI:
    """The FastAPI admin app over already-built stores (pure factory).

    `subjects` is the controlled-vocabulary registry; `retriever` (the semantic
    index, or None when semantic memory is off) is reset and re-indexed on a fact
    write so recall reflects the edit. `archive` (the item archive, or None) keeps
    the durable fact-sheet snapshot current on admin fact edits, matching the chat
    path. No CORS: the only caller is the server-side BFF, never a browser directly.

    `memory_manager` is the same orchestrator the chat path uses, wired here so an
    operator can trigger a session-summary fold, a curation pass, or a session
    flush from the UI. When it's None, or when it lacks a model-backed summarizer/
    curator (e.g. standalone `python main.py admin`), those trigger endpoints 503
    instead of pretending to run.
    """
    app = FastAPI(title="magi-admin", version="1")

    def _facts(mem: ScopedMemory) -> list[Fact]:
        return [
            Fact(id=str(f.get("id", "")), text=str(f.get("text", "")), ts=str(f.get("ts", "")))
            for f in mem.long_term_facts.read()
        ]

    def _check_version(mem: ScopedMemory, expected: Optional[str]) -> None:
        if expected is not None and expected != mem.long_term_facts.version():
            raise HTTPException(status_code=409, detail="stale version; refetch the profile")

    def _reindex_facts(mem: ScopedMemory) -> None:
        """Rebuild the user's long-term semantic slice from the current facts, so a
        deleted/edited fact stops surfacing in recall, and refresh the durable
        fact-sheet snapshot in the item archive. Each side no-ops when its backend is
        off, so an admin fact edit stays consistent with the chat path."""
        if retriever is not None:
            retriever.reset(mem.user_id, _LONG_TERM_KIND)
            for text in mem.long_term_facts.texts():
                retriever.index(mem.user_id, _LONG_TERM_KIND, text)
        if archive is not None:
            path = mem.long_term_facts.path
            data = path.read_bytes() if path.exists() else b"[]"
            archive.persist(
                "memory",
                _mem_slug(mem.user_id),
                data=data,
                content_type="application/json",
                metadata={"file": "long_term_facts.json", "user_id": mem.user_id},
            )

    def _facts_result(mem: ScopedMemory) -> FactsResult:
        return FactsResult(facts=_facts(mem), version=mem.long_term_facts.version())

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

    @app.post(
        "/admin/v1/knowledge/documents",
        response_model=IngestResult,
        dependencies=[Depends(require_auth)],
    )
    def ingest_document(body: IngestDocument) -> IngestResult:
        if body.subject and not any(s.name == body.subject for s in subjects.list()):
            raise HTTPException(status_code=422, detail=f"unknown subject {body.subject!r}")
        doc_id = (body.doc_id or _slug(body.title)).strip() or _slug(body.title)
        n = knowledge.index_document(
            doc_id,
            body.text,
            source=body.title,
            title=body.title,
            subject=body.subject,
            tags=body.tags,
        )
        return IngestResult(doc_id=doc_id, chunks_indexed=n)

    # Suffix routes (.../subject, .../tags) and /tags are declared BEFORE the
    # catch-all `{doc_id:path}` document routes: the path converter is greedy, so a
    # bare `{doc_id:path}` would otherwise swallow `<id>/tags` and shadow these.
    @app.get(
        "/admin/v1/knowledge/tags",
        response_model=TagList,
        dependencies=[Depends(require_auth)],
    )
    def list_tags() -> TagList:
        return TagList(tags=knowledge.list_tags())

    @app.put(
        "/admin/v1/knowledge/documents/{doc_id:path}/subject",
        response_model=DocumentDetailOut,
        dependencies=[Depends(require_auth)],
    )
    def set_document_subject(doc_id: str, body: SetSubject) -> DocumentDetailOut:
        # A non-empty subject must exist in the registry (controlled vocabulary).
        if body.subject and not any(s.name == body.subject for s in subjects.list()):
            raise HTTPException(status_code=422, detail=f"unknown subject {body.subject!r}")
        if not knowledge.set_document_subject(doc_id, body.subject):
            raise HTTPException(status_code=404, detail="document not found")
        detail = knowledge.get_document(doc_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="document not found")
        return DocumentDetailOut.of(detail)

    @app.patch(
        "/admin/v1/knowledge/documents/{doc_id:path}/tags",
        response_model=DocumentDetailOut,
        dependencies=[Depends(require_auth)],
    )
    def edit_document_tags(doc_id: str, body: EditTags) -> DocumentDetailOut:
        if knowledge.tag_document(doc_id, add=body.add, remove=body.remove) is None:
            raise HTTPException(status_code=404, detail="document not found")
        detail = knowledge.get_document(doc_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="document not found")
        return DocumentDetailOut.of(detail)

    @app.get(
        "/admin/v1/knowledge/documents/{doc_id:path}",
        response_model=DocumentDetailOut,
        dependencies=[Depends(require_auth)],
    )
    def get_document(doc_id: str) -> DocumentDetailOut:
        detail = knowledge.get_document(doc_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="document not found")
        return DocumentDetailOut.of(detail)

    @app.patch(
        "/admin/v1/knowledge/documents/{doc_id:path}",
        response_model=DocumentDetailOut,
        dependencies=[Depends(require_auth)],
    )
    def rename_document(doc_id: str, body: RenameDocument) -> DocumentDetailOut:
        if not knowledge.rename_document(doc_id, body.title):
            raise HTTPException(status_code=404, detail="document not found")
        detail = knowledge.get_document(doc_id)
        if detail is None:  # raced with a delete — treat as gone
            raise HTTPException(status_code=404, detail="document not found")
        return DocumentDetailOut.of(detail)

    @app.delete(
        "/admin/v1/knowledge/documents/{doc_id:path}",
        status_code=204,
        dependencies=[Depends(require_auth)],
    )
    def delete_document(doc_id: str) -> None:
        if not knowledge.delete_document(doc_id):
            raise HTTPException(status_code=404, detail="document not found")

    # --- subjects (the controlled vocabulary) ------------------------------
    @app.get(
        "/admin/v1/knowledge/subjects",
        response_model=SubjectListOut,
        dependencies=[Depends(require_auth)],
    )
    def list_subjects() -> SubjectListOut:
        return SubjectListOut(subjects=[SubjectOut.of(s) for s in subjects.list()])

    @app.post(
        "/admin/v1/knowledge/subjects",
        response_model=SubjectOut,
        dependencies=[Depends(require_auth)],
    )
    def create_subject(body: CreateSubject) -> SubjectOut:
        created = subjects.create(body.name, body.description)
        if created is None:
            raise HTTPException(status_code=409, detail="a subject with that name already exists")
        return SubjectOut.of(created)

    @app.patch(
        "/admin/v1/knowledge/subjects/{subject_id}",
        response_model=SubjectOut,
        dependencies=[Depends(require_auth)],
    )
    def edit_subject(subject_id: str, body: EditSubject) -> SubjectOut:
        before = subjects.get(subject_id)
        if before is None:
            raise HTTPException(status_code=404, detail="subject not found")
        updated = subjects.rename(subject_id, name=body.name, description=body.description)
        if updated is None:
            raise HTTPException(status_code=404, detail="subject not found")
        # Cascade a name change to the corpus so tagged docs keep their grouping.
        if body.name is not None and updated.name != before.name:
            knowledge.rename_subject(before.name, updated.name)
        return SubjectOut.of(updated)

    @app.delete(
        "/admin/v1/knowledge/subjects/{subject_id}",
        status_code=204,
        dependencies=[Depends(require_auth)],
    )
    def delete_subject(subject_id: str) -> None:
        if not subjects.delete(subject_id):
            raise HTTPException(status_code=404, detail="subject not found")

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
            facts=_facts(mem),
            raw_long_term=mem.long_term.bodies(),
            episodes=mem.episodes.bodies(),
            version=mem.long_term_facts.version(),
        )

    @app.post(
        "/admin/v1/memory/users/{user_id}/facts",
        response_model=FactsResult,
        dependencies=[Depends(require_auth)],
    )
    def add_fact(user_id: str, body: AddFact) -> FactsResult:
        mem = memory.scoped(user_id, _USER_SCOPE_SID)
        _check_version(mem, body.expected_version)
        mem.long_term_facts.add(body.text.strip())
        _reindex_facts(mem)
        return _facts_result(mem)

    @app.patch(
        "/admin/v1/memory/users/{user_id}/facts/{fact_id}",
        response_model=FactsResult,
        dependencies=[Depends(require_auth)],
    )
    def update_fact(user_id: str, fact_id: str, body: UpdateFact) -> FactsResult:
        mem = memory.scoped(user_id, _USER_SCOPE_SID)
        _check_version(mem, body.expected_version)
        if not mem.long_term_facts.update(fact_id, body.text.strip()):
            raise HTTPException(status_code=404, detail="fact not found")
        _reindex_facts(mem)
        return _facts_result(mem)

    @app.delete(
        "/admin/v1/memory/users/{user_id}/facts/{fact_id}",
        response_model=FactsResult,
        dependencies=[Depends(require_auth)],
    )
    def delete_fact(
        user_id: str,
        fact_id: str,
        expected_version: Optional[str] = Query(default=None),
    ) -> FactsResult:
        mem = memory.scoped(user_id, _USER_SCOPE_SID)
        _check_version(mem, expected_version)
        if not mem.long_term_facts.remove(fact_id):
            raise HTTPException(status_code=404, detail="fact not found")
        _reindex_facts(mem)
        return _facts_result(mem)

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

    # --- operator-triggered memory passes ---------------------------------
    # These run the same session-summary fold / curation / flush the chat path
    # runs automatically, but on demand for a chosen session. The two model-backed
    # passes (summarize, curate) 503 when no brain is wired (standalone admin); the
    # flush is model-free and always available once a manager is present.
    def _require_manager() -> MemoryManager:
        if memory_manager is None:
            raise HTTPException(
                status_code=503,
                detail="memory triggers unavailable: no memory manager wired into this admin app",
            )
        return memory_manager

    @app.post(
        "/admin/v1/memory/users/{user_id}/sessions/{session_id}/summarize",
        response_model=MemoryTriggerResult,
        dependencies=[Depends(require_auth)],
    )
    async def summarize_session(user_id: str, session_id: str) -> MemoryTriggerResult:
        mgr = _require_manager()
        if not mgr.session_summary_enabled:
            raise HTTPException(
                status_code=503,
                detail="session summarization unavailable in this deployment (no model wired)",
            )
        mgr.set_scope(user_id, session_id)
        summary = await mgr.summarize_session_now()
        return MemoryTriggerResult(
            action="summarize",
            changed=summary is not None,
            detail=(
                "Folded the pending buffer into the rolling session summary."
                if summary is not None
                else "Nothing pending to summarize."
            ),
        )

    @app.post(
        "/admin/v1/memory/users/{user_id}/sessions/{session_id}/curate",
        response_model=MemoryTriggerResult,
        dependencies=[Depends(require_auth)],
    )
    async def curate_session(user_id: str, session_id: str) -> MemoryTriggerResult:
        mgr = _require_manager()
        if not mgr.curation_enabled:
            raise HTTPException(
                status_code=503,
                detail="memory curation unavailable in this deployment (no model wired)",
            )
        mgr.set_scope(user_id, session_id)
        applied = await mgr.curate_session_summary()
        # A curator profile change lands on the user-level fact sheet; keep the
        # admin's semantic slice + archive snapshot consistent, as fact edits do.
        if applied and "profile" in applied:
            _reindex_facts(memory.scoped(user_id, _USER_SCOPE_SID))
        return MemoryTriggerResult(
            action="curate",
            changed=bool(applied),
            detail=(
                f"Curated durable memory: {', '.join(applied)}."
                if applied
                else "No durable changes (empty session summary or nothing to keep)."
            ),
        )

    @app.post(
        "/admin/v1/memory/users/{user_id}/sessions/{session_id}/flush",
        response_model=MemoryTriggerResult,
        dependencies=[Depends(require_auth)],
    )
    async def flush_session(user_id: str, session_id: str) -> MemoryTriggerResult:
        mgr = _require_manager()
        mgr.set_scope(user_id, session_id)
        dropped = mgr.flush_session()
        return MemoryTriggerResult(
            action="flush",
            changed=dropped > 0,
            detail=(
                f"Flushed {dropped} live turn(s); the rolling summary was carried into an episode."
                if dropped
                else "Nothing to flush (no live turns)."
            ),
        )

    @app.get(
        "/admin/v1/memory/persona",
        response_model=Persona,
        dependencies=[Depends(require_auth)],
    )
    def get_persona() -> Persona:
        return Persona(text=memory.persona.read())

    # --- bot identity (name / description / profile picture) ---------------
    def _identity_out() -> IdentityOut:
        store = memory.identity
        ident = store.read()
        return IdentityOut(
            display_name=ident.display_name,
            description=ident.description,
            has_avatar=ident.has_avatar,
            avatar_mime=ident.avatar_mime,
            avatar_filename=ident.avatar_filename,
            version=store.version(),
        )

    def _check_identity_version(expected: Optional[str]) -> None:
        if expected is not None and expected != memory.identity.version():
            raise HTTPException(status_code=409, detail="stale version; refetch the identity")

    @app.get(
        "/admin/v1/identity",
        response_model=IdentityOut,
        dependencies=[Depends(require_auth)],
    )
    def get_identity() -> IdentityOut:
        return _identity_out()

    @app.put(
        "/admin/v1/identity",
        response_model=IdentityOut,
        dependencies=[Depends(require_auth)],
    )
    def put_identity(body: UpdateIdentity) -> IdentityOut:
        _check_identity_version(body.expected_version)
        memory.identity.set_fields(
            display_name=body.display_name, description=body.description
        )
        return _identity_out()

    @app.put(
        "/admin/v1/identity/avatar",
        response_model=IdentityOut,
        dependencies=[Depends(require_auth)],
    )
    def put_identity_avatar(body: SetAvatar) -> IdentityOut:
        _check_identity_version(body.expected_version)
        payload = body.data_base64
        if payload.startswith("data:"):  # tolerate a full data: URI
            payload = payload.partition(",")[2]
        try:
            data = base64.b64decode(payload)
        except ValueError as exc:  # binascii.Error subclasses ValueError
            raise HTTPException(status_code=422, detail="invalid base64 image data") from exc
        if not data:
            raise HTTPException(status_code=422, detail="empty image data")
        try:
            memory.identity.set_avatar(data, body.mime_type, body.filename)
        except ValueError as exc:  # unsupported mime type
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _identity_out()

    @app.delete(
        "/admin/v1/identity/avatar",
        response_model=IdentityOut,
        dependencies=[Depends(require_auth)],
    )
    def delete_identity_avatar(expected_version: Optional[str] = Query(default=None)) -> IdentityOut:
        _check_identity_version(expected_version)
        memory.identity.clear_avatar()
        return _identity_out()

    @app.get("/admin/v1/identity/avatar", dependencies=[Depends(require_auth)])
    def get_identity_avatar() -> Response:
        """The current profile-picture bytes (404 when none) — lets the settings
        page preview without going through the chat surface."""
        avatar = memory.identity.avatar_bytes()
        if avatar is None:
            raise HTTPException(status_code=404, detail="no avatar set")
        data, mime = avatar
        return Response(content=data, media_type=mime, headers={"Cache-Control": "no-cache"})

    # --- raw-file editor: full-CRUD power on the plumbing kinds ------------
    def _raw_target(kind: str, user_id: Optional[str], session_id: Optional[str]):
        """Resolve (path, is_json, reindex_kind) for an editable file kind, or raise
        a 4xx. `reindex_kind` names the semantic slice to rebuild after a write
        (None when the kind isn't mirrored)."""
        if kind == "persona":
            return memory.persona.path, False, None
        if not user_id:
            raise HTTPException(status_code=422, detail="user_id required for this kind")
        mem = memory.scoped(user_id, session_id or _USER_SCOPE_SID)
        if kind == "episodes":
            return mem.episodes.path, False, "episode"
        if kind == "raw_long_term":
            return mem.long_term.path, False, None
        if kind in ("session_window", "session_summary", "session_pending"):
            if not session_id:
                raise HTTPException(status_code=422, detail="session_id required for this kind")
            return {
                "session_window": (mem.live_turns.path, True, None),
                "session_summary": (mem.session_summary.path, False, None),
                "session_pending": (mem.pending.path, True, None),
            }[kind]
        raise HTTPException(status_code=404, detail=f"unknown file kind {kind!r}")

    @app.get(
        "/admin/v1/memory/files/{kind}",
        response_model=RawFile,
        dependencies=[Depends(require_auth)],
    )
    def get_raw_file(
        kind: str,
        user_id: Optional[str] = Query(default=None),
        session_id: Optional[str] = Query(default=None),
    ) -> RawFile:
        path, _is_json, _reindex = _raw_target(kind, user_id, session_id)
        content = path.read_text(encoding="utf-8") if path.exists() else ""
        return RawFile(kind=kind, content=content, version=_file_version(path))

    @app.put(
        "/admin/v1/memory/files/{kind}",
        response_model=RawFile,
        dependencies=[Depends(require_auth)],
    )
    def put_raw_file(
        kind: str,
        body: PutRawFile,
        user_id: Optional[str] = Query(default=None),
        session_id: Optional[str] = Query(default=None),
    ) -> RawFile:
        path, is_json, reindex = _raw_target(kind, user_id, session_id)
        if body.expected_version is not None and body.expected_version != _file_version(path):
            raise HTTPException(status_code=409, detail="stale version; refetch the file")
        if is_json:
            # Validate-on-save: JSON kinds (turn windows) must parse to a list, so a
            # bad paste can't park content the chat layer will choke on.
            import json

            try:
                parsed = json.loads(body.content)
            except json.JSONDecodeError as exc:
                raise HTTPException(status_code=422, detail=f"invalid JSON: {exc}") from exc
            if not isinstance(parsed, list):
                raise HTTPException(status_code=422, detail="expected a JSON list of turns")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body.content, encoding="utf-8")
        if reindex == "episode" and retriever is not None and user_id:
            mem = memory.scoped(user_id, session_id or _USER_SCOPE_SID)
            retriever.reset(user_id, "episode")
            for body_text in mem.episodes.bodies():
                retriever.index(user_id, "episode", body_text)
        return RawFile(kind=kind, content=body.content, version=_file_version(path))

    return app


def _turn(t: dict) -> dict:
    """A stored turn dict narrowed to the wire fields (tolerates missing keys)."""
    return {
        "role": str(t.get("role", "")),
        "content": str(t.get("content", "")),
        "ts": str(t.get("ts", "")),
    }


def _slug(title: str) -> str:
    """A filesystem/url-safe doc_id derived from a title (lowercased, hyphenated)."""
    import re

    s = re.sub(r"[^a-z0-9]+", "-", title.strip().lower()).strip("-")
    return s or "document"


def _file_version(path) -> str:
    """A content version token for any memory file — sha256 of its bytes (empty-file
    token when absent). The optimistic-concurrency token for the raw editor."""
    import hashlib

    raw = path.read_bytes() if path.exists() else b""
    return hashlib.sha256(raw).hexdigest()


def build_admin_app(memory_manager: Optional[MemoryManager] = None) -> FastAPI:
    """Composition root: the real stores from config, served over HTTP.

    Both stores are built unconditionally (admin manages memory + the corpus
    regardless of whether the chat-time tools are enabled — the same reasoning as
    `scripts/ingest_knowledge.py`).

    `memory_manager` is the chat stack's orchestrator, passed by a channel that
    mounts this app in-process (the HTTP API, Discord) so the operator triggers run
    the model-backed summarizer/curator. Standalone (`python main.py admin`) leaves
    it None: we build a model-free manager instead, so the flush trigger still works
    and the summarize/curate triggers 503 honestly rather than silently no-op."""
    from magi.core.config import config
    from magi.core.items import build_item_archive_from_config
    from magi.core.memory import build_memory_from_config
    from magi.core.memory.semantic import build_semantic_index

    log_info("building admin app")
    if config.admin_auth_token is None:
        log_info("admin: auth DISABLED (ADMIN_AUTH_TOKEN not set) — keep the port unpublished")
    # A model-free manager over the same on-disk tree when the caller didn't hand us
    # the chat stack's own (its summarizer/curator stay None, so those triggers 503).
    manager = memory_manager or build_memory_from_config()
    # Same semantic index as the chat stack (None when semantic memory is off), so
    # an admin fact edit re-indexes the same slice the lead retrieves from. One item
    # archive (None unless enabled) is shared by the knowledge store and the fact
    # endpoints, so knowledge deletes cascade to the stored original and fact edits
    # keep the durable snapshot current.
    archive = build_item_archive_from_config()
    return create_admin_app(
        KnowledgeStore(archive=archive),
        manager.store,
        SubjectRegistry(config.knowledge_subjects_path),
        retriever=build_semantic_index(),
        auth_token=config.admin_auth_token,
        archive=archive,
        memory_manager=manager,
    )
