# Purpose

The `magi` engine: one assembled stack (memory + team + tools) that every channel
drives. Import package is `magi` (distribution `magi-ai-assistant`). A private
persona overlay installs this as a dependency and extends it from the outside.

# Local Contracts

- **Dependency direction is strictly downward**: `cli` → `channels` → `agent` →
  `core`. `core` depends on nothing above it. Never import `agent`, `channels`,
  or `cli` from `core`.
- **`core/` is model-free.** Anything needing an LLM (curator, summarizers) lives
  in `agent/` and is passed into `core` as an injected callable, never imported.
- **Dependency injection, no globals.** Team, `MemoryManager`, and DB are built at
  composition roots (`channels/bootstrap.py`) and passed in. The only ambient state
  is the per-message memory **scope**, carried via a `ContextVar` — never a tool
  argument.
- **Typed config: file or code.** `Config` (`core/config.py`) is a frozen
  pydantic model. A deployment sets it from a YAML file (`./magi.yaml` or
  `$MAGI_HOME/config.yaml`, `core/config_file.py`) and/or `configure(...)` at
  the entrypoint (code wins). Both paths validate names and types; nested
  groups merge per key (`derive`). Only
  *secrets* come from `.env` (`$MAGI_HOME/.env`, `./.env`). Loading is always
  explicit — never at import. See ADR 0004.
- **Graceful degradation.** Optional backends (storage, knowledge, semantic search,
  MCP, git memory, websearch) lazy-import their heavy dep and degrade to
  "tool not attached" / no-op when absent or down. The bot must always boot. Each
  optional dep is an extra in `pyproject.toml`; keep the base install lean.
- **Extension points are registries/overlays, extended from the persona, not by
  editing this tree**: `register_member`, `register_tool` / `register_lead_toolkit`
  (`agent/tools/__init__.py`), `register_skill`, and the `load_prompt` overlay.
- **Pydantic at boundaries.** JSON from files under `core/`, upstream HTTP in
  tools, the client SDK, FastAPI bodies, and LLM output (`agent/curator.py`,
  `agent/mood.py`) is validated with a pydantic `BaseModel`/`TypeAdapter`, and
  a `ValidationError` follows the same degrade path as bad JSON. Frozen
  `@dataclass` stays fine for internal value types. Free-form JSON is typed
  `JsonValue` / `JsonObject` (`core/types.py`), never `Any`; degrade-on-bad-input
  loaders use `parse_json_object` / `parse_json_array` (None on bad input), hard
  boundaries use the raising `JSON_OBJECT` / `JSON_VALUE` adapters.
- **Structural contracts over base classes.** `Runner` (`core/conversation.py`) and
  `PlatformAdapter` (`channels/gateway.py`) are narrow `Protocol`s satisfied by
  shape.

# Work Guidance

- Prompts live in `prompts/` as markdown package data, resolved through the overlay
  in `core/prompts.py` (`load_prompt`) — a persona ships its own; do not hardcode
  prompt text in Python.
- `desktop/` and `client/` are optional surfaces (PySide6 desktop shell; the
  embedded/http/sync client SDK). `desktop` is behind the `desktop` extra and
  imported lazily only when that channel runs.

# Verification

`uv run ruff check .`, `uv run ruff format --check .`, `uv run basedpyright`,
and `uv run pytest -q` (from repo root, after `uv sync --dev --all-extras`).

# Child Index

- `cli/` — the `magi` command (setup wizard, run, config, doctor); own child
  doc.
- `core/` — model-free mechanism (conversation runner, config + config file,
  memory, knowledge, storage, db, media, embeddings, skills library, session
  index, command sandbox).
- `agent/` — model-bound brain (team, members, model builders, curator,
  summarizers, tools registry).
- `channels/` — transport adapters over the `PlatformAdapter` gateway.

Owned here (no child doc): `prompts/`, `client/`, `desktop/`.
