# 0002 — Admin interface for agent memory & knowledge

- Status: Accepted
- Date: 2026-06-29
- Builds on: ADR 0001 (per-kind memory modules), `core/knowledge` (knowledge layer),
  `channels/api.py` (HTTP channel pattern)

## Context

We want an operator-facing way to **view and manage** the agent's per-user memory
and to **organize the knowledge corpus** (add sources, group them, tag them) so the
model can query and filter what it needs. Two gaps make this non-trivial today:

- **Knowledge has no document-level view.** A "document" is just N Qdrant chunks
  sharing a `doc_id`; `KnowledgeStore` can `search` and `delete_document`/
  `index_document`, but cannot *enumerate* documents. Chunks carry no human title,
  no topic grouping, no tags — only `doc_id`, `source`, `scope` (reserved for
  per-user origin), `chunk_index`, `text`, `metadata`, `ts`. Ingest is out-of-band
  (`scripts/ingest_knowledge.py`), reads server-side files only.
- **Memory is curator-owned and lock-free.** Per-user files under `data/memory`
  (`long_term_facts.json`, `long_term.md`, `episodic.md`, per-session window /
  summary / pending) plus a **global** `persona.md`; written constantly by the
  channel + post-turn curator with whole-file replaces and no concurrency control.
  The optional Qdrant memory mirror (`SemanticIndex`) is **index + search only** —
  no delete/update — so it already drifts (deleted/updated facts linger as ghost
  vectors). There is no "list users" capability.

## Decision

A new **separate admin service** that reuses `magi` core modules, fronted by a
Next.js BFF. Twenty decisions, grouped:

### Shape & placement
1. **Audience: single operator/admin.** One credential, no per-user portal, no
   multi-tenant UI. The `user_id` scoping in the store stays the seam for a future
   per-user view.
2. **Separate deployable, shared code.** Keeps the write-capable admin surface off
   the public chat brain. Not a separate codebase — a new `magi` channel +
   entrypoint, importing `FileMemoryStore` / `KnowledgeStore` / `SemanticIndex`
   directly (no team/LLM; it never runs the model).
3. **Minimal repo footprint.** `src/magi/channels/admin.py` (`create_admin_app` /
   `build_admin_app`, mirroring `api.py`), `main_admin.py` + `main_admin_docker.py`,
   and one new `web/` dir for the Next.js app. No `apps/`+`packages/` restructure
   until the frontend has real shared packages.
17. **BFF topology.** browser → Next.js route handlers/server actions → Python
    admin API. `ADMIN_AUTH_TOKEN` lives **server-side in Next.js**, never shipped to
    the browser; operator logs in with a password → httpOnly session cookie. Single
    origin → no CORS. Python trusts the bearer the BFF presents.
18. **Type sharing via OpenAPI.** Pydantic is the single source of truth; FastAPI
    emits OpenAPI; `openapi-typescript` regenerates `web/src/lib/api-types.ts`
    (`pnpm gen:api` + a CI staleness check). No hand-maintained TS.
19. **Deploy: internal-only admin-api, published web, opt-in profile.** Add
    `admin-api` (unpublished — reachable only at `http://admin-api:8000` on the
    compose network) and `web` (publishes a host port) to `docker-compose.app.yaml`
    behind `--profile admin` (like `discord`). The network enforces the BFF model:
    only Next.js is externally reachable; the token-bearing Python surface stays
    inside. admin-api is a **separate process** over the same `./data` + Qdrant —
    cross-process file races are handled by optimistic concurrency (below).

### Knowledge
4. **Derived listing.** New `KnowledgeStore.list_documents()` scrolls Qdrant
   payloads (no vectors) and groups by `doc_id` → `{title, source, subject, tags,
   scope, chunk_count, latest_ts}`. The chunks stay the single source of truth (no
   manifest to drift). Implementation note: `delete_document`/list must force the
   lazy client (`_ensure_client`) on a cold process.
5. **Rename = edit a display label, not identity.** Promote a first-class `title`
   payload field (defaults to `source`); editing it is an in-place Qdrant
   `set_payload` over the doc's points — no re-embedding. `doc_id` stays the
   immutable key (replace-on-reingest, delete depend on it).
6. **Add sources via a resolver seam.** Paste-text and file-upload first (URL fetch
   as the immediate fast-follow, connectors later), each a resolver
   `Source → (title, text)` behind one ingest endpoint feeding `index_document`.
   `scope` stays `"global"` (the per-user scope seam stays reserved, not exposed).
7. **Subject (single) + tags (multi), new payload fields** orthogonal to the
   reserved `scope`. A doc has exactly one subject and zero-or-more tags.
8. **Subjects controlled, tags free-form.** Subjects are a managed, low-cardinality
   vocabulary backed by a small registry (`subjects.json`: `{id, name,
   description?}`) with admin CRUD; a doc picks one. Tags are typed freely with
   autocomplete **derived** from the corpus (no registry).
9. **Two model tools** (one workflow each):
   - `search_knowledge(query, subject?, tags?)` — find/filter; results carry their
     subject+tags so the model learns the live vocabulary in-context.
   - `tag_knowledge(doc_id, add_tags?, remove_tags?)` — the model's **tag** write
     path.
   **Write boundary:** the model writes **tags only**. Content (verbatim chunks) and
   the subject spine stay admin-only. This deliberately relaxes the "knowledge is
   read-only at chat time" invariant **for the tag layer only** — content is still
   never LLM-rewritten.
10. **Filter semantics: subject excludes, tags boost.** `subject` → hard Qdrant
    `MatchValue` filter (restricts candidates). `tags` → **soft** signal: never
    exclude, only re-rank.
11. **Tag boost via Python re-rank.** Query Qdrant with the subject filter,
    over-fetch (`top_k × 4`) by vector similarity, then blend in Python:
    `blended = vector_score + tag_weight × (matched_tags / max(1, len(query_tags)))`,
    `tag_weight` a config knob (`[[config-code-first]]`). Empty filtered result →
    fall back to an unfiltered similarity pass, noted. Qdrant server-side scoring is
    a later optimization behind the same tool contract.

### Memory
13. **Full CRUD on all kinds.**
14. **Hybrid editor surface.** Structured per-fact editor for `long_term_facts.json`
    (reuses `JsonFacts.add/update/remove`). Everything else (episodes, persona,
    session window/summary/pending) via a **raw-file editor with validate-on-save**
    (JSON must parse to the expected shape; markdown written verbatim) — one generic
    get-file / put-file pair, no per-kind typed endpoints.
15. **Optimistic concurrency.** GET returns each file with a version token (content
    hash or `st_mtime_ns`); PUT must echo it; backend returns **409** on mismatch →
    UI refetches and the operator retries. Guards the slow human edit against the
    agent's hot path without locks. Residual ~1ms window self-corrects next turn.
16. **Semantic mirror sync on write.** On any admin memory write, slice-re-index the
    affected `(user_id, kind)`: new `SemanticIndex.reset(user_id, kind)`
    (delete-by-filter) + re-embed current entries. No-op when `semantic_memory` is
    off. (Routing the *curator's* delete/update through the same `reset` would fix
    the pre-existing ghost-vector drift too — optional scope.)
    Enumeration: filesystem scan of `users/` for the user list; per-user session
    list from `sessions/`.

### UI
20. **Two sections matching the two domains.**
    - **Memory:** user list → user detail tabs (*Profile facts* structured CRUD /
      *Episodes* raw / *Sessions* list → raw view/edit) + a **single global Persona**
      screen at top level (persona is global, not per-user).
    - **Knowledge:** subject rail → document list (subject filter, tag search) →
      document detail (title/subject/tags edit, chunk/source view) + Add knowledge +
      Manage subjects.

## Consequences

New core primitives required (each a small, testable addition that the ingest CLI
and existing tools also benefit from):

- `KnowledgeStore.list_documents()`; `title` / `subject` / `tags` payload fields +
  `set_payload`-based title/tag edits; the Python tag-boost re-rank.
- A subject registry (`subjects.json`) + CRUD.
- `SemanticIndex.reset(user_id, kind)` (delete-by-filter).
- Memory `list_users()` / `list_sessions()`; per-file get/put with a concurrency
  token.
- Two model tools (`search_knowledge` gains `subject`/`tags`; new `tag_knowledge`).
- `channels/admin.py` FastAPI app + Next.js BFF + two compose services.

Supersedes parts of the recorded design: knowledge is no longer strictly read-only
at chat time (the model maintains tags); the knowledge schema gains `title` /
`subject` / `tags` beyond `scope`. Builds on ADR 0001's per-kind structure — the
admin write paths reuse each kind's existing IO rather than inventing new ones.

Tests follow the existing per-module pattern (`tests/test_knowledge.py`,
`tests/test_memory*.py`, `tests/test_api.py`): list/rename/tag-filter on the store,
concurrency-token 409 on the admin app, resolver seam on ingest.
