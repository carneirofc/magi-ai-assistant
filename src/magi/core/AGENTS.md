# Purpose

The **model-free mechanism** of the engine: the conversation lifecycle, config,
and the durable subsystems (memory, knowledge, storage, db, media). This is the
layer a persona reuses unchanged.

# Local Contracts

- **Import nothing model-bound.** No LLM SDKs, no `magi.agent`, no `magi.channels`.
  A step that needs a model (curation, summarization) is received as an injected
  callable (`CurateFn`, the summarizer seam), constructed in `agent/` and handed in
  at composition — mirror that pattern for any new model-touching hook.
- **Scope is ambient, set once per message** via `set_scope(user, session)` and read
  through a process-global `ContextVar`. The `MemoryManager` is a single shared
  instance; resolve `mem` per-access, never cache it, and never pass scope as a
  function/tool argument (prevents cross-user leakage by construction).
- **Context rides inside the run input, never on a shared runner.** Mutating shared
  runner state would race concurrent conversations.
- **Failures never hand the channel silence** (`conversation.py`): a run that errors
  returns an honest error reply, an empty run returns a fallback, and curation
  failures are swallowed — they must never break a chat.

# Work Guidance

- **Logging in new code**: import `log_info` / `log_warning` from `core/log.py`
  (typed wrappers over agno's logger) so strict typing stays clean.
- **Session index (`session_index.py`)**: FTS5 over finished turns, written by
  `ConversationService` through the `TurnIndexer` protocol; reads are always
  filtered by the scoped `user_id`, and user text is quoted into the MATCH
  expression (never raw FTS5 syntax). Indexing failures are logged, never raised.
- **Skills library (`skills_fs.py`)**: SKILL.md parsing, discovery, and
  atomic writes — pure IO; approved `skill` proposals (`evolution.py`) land
  through `write_skill`.
- **Config (`config.py`, `config_file.py`)**: add a setting as a typed field on
  `Config` (use `Literal`/nested `BaseModel` for closed sets and groups, `_secret`
  for env-only values). File loading stays in `config_file.py`, pure IO.

- **Memory (`memory/`)** is deliberate: durable, inspectable files the model
  reads/writes on purpose, never auto-extracted. It is per-kind (long-term,
  episode, session, persona) — one kind = one module with its own storage, render,
  and optional fold. The assembler owns section order and headers. See
  [../../../CONTEXT.md](../../../CONTEXT.md) for the authoritative vocabulary
  (kind, fold, curate, scope, live window, pending buffer) and
  [ADR 0001](../../../docs/adr/0001-per-kind-memory-modules.md).
- **Knowledge (`knowledge/`)** is a global, read-only RAG *document* corpus —
  distinct from memory. Chunked + embedded faithfully (no LLM extraction) into
  Qdrant; populated out-of-band by `scripts/ingest_knowledge.py`; gated by
  `knowledge_enabled`; degrades to no tool when off / Qdrant down.
- **Storage (`storage/`)**, **git memory backend**, and **semantic search** are all
  optional-extra backends behind a factory that no-ops when the dep/config is
  absent. Preserve that degrade-don't-crash contract.

# Verification

`uv run pytest -q` (memory/knowledge/storage each have dedicated `tests/test_*`).
