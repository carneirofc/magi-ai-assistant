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
from pathlib import Path
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
from magi.core.memory import (
    InvalidRawJsonError,
    MemoryManagerRequiredError,
    MemoryManager,
    MemoryAdmin,
    SessionRequiredError,
    StaleVersionError,
    TriggerUnavailableError,
    UnknownMemoryFileKindError,
    UserRequiredError,
    resolve_memory_settings,
)
from magi.core.memory.semantic import MemoryRetriever
from magi.core.memory.store import FileMemoryStore
from magi.core.settings import MemoryOverrides, OperatorSettingsStore

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


# --- operator settings wire format -------------------------------------------
class MemorySettingsOut(BaseModel):
    """The effective memory settings (operator overrides overlaid on code defaults)
    the UI edits. `active_memory_dir` is where the RUNNING process actually reads/
    writes memory; when it differs from `memory_dir`, a saved change is pending and
    `restart_required` is true — these settings apply at startup, not live. `version`
    is the optimistic-concurrency token; echo it on a write or risk a 409."""

    memory_dir: str = Field(description="Where memory will live (as the operator typed it; ~ allowed).")
    git_enabled: bool = Field(description="Whether the memory tree is a git repo committed on every write.")
    git_author_name: str = Field(description="Commit author name for git-versioned memory.")
    git_author_email: str = Field(description="Commit author email for git-versioned memory.")
    active_memory_dir: str = Field(description="The resolved dir the running process is using right now.")
    restart_required: bool = Field(description="True when saved settings differ from the running process.")
    version: str = ""


class UpdateMemorySettings(BaseModel):
    """Set the memory location + git-versioning. Applied on the next restart. An empty
    `memory_dir` clears the override (back to the code default)."""

    memory_dir: str = Field(default="", description="Memory directory ('' = use the code default).")
    git_enabled: bool = Field(default=False, description="Enable git-versioned memory.")
    git_author_name: str = Field(default="", description="Commit author name ('' = code default).")
    git_author_email: str = Field(default="", description="Commit author email ('' = code default).")
    expected_version: Optional[str] = Field(
        default=None, description="The version from the last read; rejected with 409 if stale."
    )


def create_admin_app(
    knowledge: KnowledgeStore,
    memory: FileMemoryStore,
    subjects: SubjectRegistry,
    retriever: Optional[MemoryRetriever] = None,
    auth_token: Optional[str] = None,
    archive: Optional[ItemArchive] = None,
    memory_manager: Optional[MemoryManager] = None,
    settings_store: Optional[OperatorSettingsStore] = None,
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

    `settings_store` (or None) persists operator overrides — where memory lives and
    its git-versioning — that the `/settings/memory` routes read/write; those apply on
    the next restart, so the running `memory` root is reported back as the active one.
    """
    app = FastAPI(title="magi-admin", version="1")
    memory_admin = MemoryAdmin(memory, retriever=retriever, archive=archive)

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
        for user_id in memory_admin.list_users():
            profile = memory_admin.profile(user_id)
            users.append(
                UserSummary(
                    user_id=user_id,
                    fact_count=len(profile.facts),
                    episode_count=len(profile.episodes),
                    session_count=len(memory_admin.list_sessions(user_id)),
                )
            )
        return UserList(users=users)

    @app.get(
        "/admin/v1/memory/users/{user_id}/profile",
        response_model=Profile,
        dependencies=[Depends(require_auth)],
    )
    def get_profile(user_id: str) -> Profile:
        profile = memory_admin.profile(user_id)
        return Profile(
            facts=[Fact(id=f.id, text=f.text, ts=f.ts) for f in profile.facts],
            raw_long_term=profile.raw_long_term,
            episodes=profile.episodes,
            version=profile.version,
        )

    @app.post(
        "/admin/v1/memory/users/{user_id}/facts",
        response_model=FactsResult,
        dependencies=[Depends(require_auth)],
    )
    def add_fact(user_id: str, body: AddFact) -> FactsResult:
        try:
            result = memory_admin.add_fact(user_id, body.text, body.expected_version)
        except StaleVersionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return FactsResult(
            facts=[Fact(id=f.id, text=f.text, ts=f.ts) for f in result.facts],
            version=result.version,
        )

    @app.patch(
        "/admin/v1/memory/users/{user_id}/facts/{fact_id}",
        response_model=FactsResult,
        dependencies=[Depends(require_auth)],
    )
    def update_fact(user_id: str, fact_id: str, body: UpdateFact) -> FactsResult:
        try:
            result = memory_admin.update_fact(user_id, fact_id, body.text, body.expected_version)
        except StaleVersionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if result is None:
            raise HTTPException(status_code=404, detail="fact not found")
        return FactsResult(
            facts=[Fact(id=f.id, text=f.text, ts=f.ts) for f in result.facts],
            version=result.version,
        )

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
        try:
            result = memory_admin.delete_fact(user_id, fact_id, expected_version)
        except StaleVersionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if result is None:
            raise HTTPException(status_code=404, detail="fact not found")
        return FactsResult(
            facts=[Fact(id=f.id, text=f.text, ts=f.ts) for f in result.facts],
            version=result.version,
        )

    @app.get(
        "/admin/v1/memory/users/{user_id}/sessions",
        response_model=SessionList,
        dependencies=[Depends(require_auth)],
    )
    def list_user_sessions(user_id: str) -> SessionList:
        return SessionList(sessions=memory_admin.list_sessions(user_id))

    @app.get(
        "/admin/v1/memory/users/{user_id}/sessions/{session_id}",
        response_model=SessionDetail,
        dependencies=[Depends(require_auth)],
    )
    def get_session(user_id: str, session_id: str) -> SessionDetail:
        snapshot = memory_admin.session(user_id, session_id)
        return SessionDetail(
            turns=[Turn(role=t.role, content=t.content, ts=t.ts) for t in snapshot.turns],
            summary=snapshot.summary,
            pending=[Turn(role=t.role, content=t.content, ts=t.ts) for t in snapshot.pending],
        )

    # --- operator-triggered memory passes ---------------------------------
    # These run the same session-summary fold / curation / flush the chat path
    # runs automatically, but on demand for a chosen session. The two model-backed
    # passes (summarize, curate) 503 when no brain is wired (standalone admin); the
    # flush is model-free and always available once a manager is present.
    @app.post(
        "/admin/v1/memory/users/{user_id}/sessions/{session_id}/summarize",
        response_model=MemoryTriggerResult,
        dependencies=[Depends(require_auth)],
    )
    async def summarize_session(user_id: str, session_id: str) -> MemoryTriggerResult:
        try:
            result = await memory_admin.summarize_session(memory_manager, user_id, session_id)
        except (MemoryManagerRequiredError, TriggerUnavailableError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return MemoryTriggerResult(
            action=result.action,
            changed=result.changed,
            detail=result.detail,
        )

    @app.post(
        "/admin/v1/memory/users/{user_id}/sessions/{session_id}/curate",
        response_model=MemoryTriggerResult,
        dependencies=[Depends(require_auth)],
    )
    async def curate_session(user_id: str, session_id: str) -> MemoryTriggerResult:
        try:
            result = await memory_admin.curate_session(memory_manager, user_id, session_id)
        except (MemoryManagerRequiredError, TriggerUnavailableError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return MemoryTriggerResult(
            action=result.action,
            changed=result.changed,
            detail=result.detail,
        )

    @app.post(
        "/admin/v1/memory/users/{user_id}/sessions/{session_id}/flush",
        response_model=MemoryTriggerResult,
        dependencies=[Depends(require_auth)],
    )
    async def flush_session(user_id: str, session_id: str) -> MemoryTriggerResult:
        try:
            result = await memory_admin.flush_session(memory_manager, user_id, session_id)
        except MemoryManagerRequiredError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return MemoryTriggerResult(
            action=result.action,
            changed=result.changed,
            detail=result.detail,
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

    # --- operator settings: memory location + git-versioning (apply on restart) ---
    def _memory_settings_out() -> MemorySettingsOut:
        overrides = settings_store.read_memory() if settings_store else MemoryOverrides()
        eff = resolve_memory_settings(overrides)
        active = str(memory.root)
        # Compare resolved paths so a `~` or trailing-slash difference isn't read as
        # drift; when the saved dir differs from the one the process booted with, the
        # change is pending a restart.
        pending = str(Path(eff.memory_dir).expanduser().resolve())
        restart_required = pending != str(Path(active).resolve())
        return MemorySettingsOut(
            memory_dir=eff.raw_memory_dir,
            git_enabled=eff.git_enabled,
            git_author_name=eff.git_author_name,
            git_author_email=eff.git_author_email,
            active_memory_dir=active,
            restart_required=restart_required,
            version=settings_store.version() if settings_store else "",
        )

    @app.get(
        "/admin/v1/settings/memory",
        response_model=MemorySettingsOut,
        dependencies=[Depends(require_auth)],
    )
    def get_memory_settings() -> MemorySettingsOut:
        return _memory_settings_out()

    @app.put(
        "/admin/v1/settings/memory",
        response_model=MemorySettingsOut,
        dependencies=[Depends(require_auth)],
    )
    def put_memory_settings(body: UpdateMemorySettings) -> MemorySettingsOut:
        if settings_store is None:
            raise HTTPException(status_code=503, detail="operator settings not available")
        if body.expected_version is not None and body.expected_version != settings_store.version():
            raise HTTPException(status_code=409, detail="stale version; refetch the settings")
        # Blank strings clear the override (fall back to the code default); the git
        # toggle is always an explicit choice once saved.
        settings_store.set_memory(
            MemoryOverrides(
                memory_dir=body.memory_dir.strip() or None,
                git_enabled=body.git_enabled,
                git_author_name=body.git_author_name.strip() or None,
                git_author_email=body.git_author_email.strip() or None,
            )
        )
        return _memory_settings_out()

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
        try:
            file = memory_admin.get_raw_file(kind, user_id=user_id, session_id=session_id)
        except UserRequiredError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except SessionRequiredError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except UnknownMemoryFileKindError as exc:
            raise HTTPException(status_code=404, detail=f"unknown file kind {kind!r}") from exc
        return RawFile(kind=file.kind, content=file.content, version=file.version)

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
        try:
            file = memory_admin.put_raw_file(
                kind,
                body.content,
                body.expected_version,
                user_id=user_id,
                session_id=session_id,
            )
        except UserRequiredError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except StaleVersionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except InvalidRawJsonError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except SessionRequiredError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except UnknownMemoryFileKindError as exc:
            raise HTTPException(status_code=404, detail=f"unknown file kind {kind!r}") from exc
        return RawFile(kind=file.kind, content=file.content, version=file.version)

    return app
def _slug(title: str) -> str:
    """A filesystem/url-safe doc_id derived from a title (lowercased, hyphenated)."""
    import re

    s = re.sub(r"[^a-z0-9]+", "-", title.strip().lower()).strip("-")
    return s or "document"


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
    from magi.core.memory import build_memory_from_config, operator_settings_store
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
        settings_store=operator_settings_store(),
    )
