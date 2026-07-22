# Purpose

The `magi` engine: one assembled stack (memory + team + tools) that every channel
drives. Import package is `magi` (distribution `magi-ai-assistant`). A private
persona overlay installs this as a dependency and extends it from the outside.

# Local Contracts

- **Dependency direction is strictly downward**: `channels` → `agent` → `core`.
  `core` depends on nothing above it. Never import `agent` or `channels` from
  `core`.
- **`core/` is model-free.** Anything needing an LLM (curator, summarizers) lives
  in `agent/` and is passed into `core` as an injected callable, never imported.
- **Dependency injection, no globals.** Team, `MemoryManager`, and DB are built at
  composition roots (`channels/bootstrap.py`) and passed in. The only ambient state
  is the per-message memory **scope**, carried via a `ContextVar` — never a tool
  argument.
- **Code-first config.** Settings are plain Python set at the entrypoint via
  `configure(...)` (`core/config.py`). Only *secrets* come from `.env`.
- **Graceful degradation.** Optional backends (storage, knowledge, semantic search,
  MCP, git memory, websearch) lazy-import their heavy dep and degrade to
  "tool not attached" / no-op when absent or down. The bot must always boot. Each
  optional dep is an extra in `pyproject.toml`; keep the base install lean.
- **Extension points are registries/overlays, extended from the persona, not by
  editing this tree**: `register_member`, `register_tool` / `register_lead_toolkit`
  (`agent/tools/__init__.py`), `register_skill`, and the `load_prompt` overlay.
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

`uv run ruff check src tests` and `uv run pytest -q` (from repo root).

# Child Index

- `core/` — model-free mechanism (conversation runner, config, memory, knowledge,
  storage, db, media, embeddings).
- `agent/` — model-bound brain (team, members, model builders, curator,
  summarizers, tools registry).
- `channels/` — transport adapters over the `PlatformAdapter` gateway.

Owned here (no child doc): `prompts/`, `client/`, `desktop/`.
