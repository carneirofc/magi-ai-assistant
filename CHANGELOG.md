# Changelog

All notable changes to **magi** are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/).

## [Unreleased]

### Added

- **Bot identity.** A global, operator-set profile the assistant presents as
  itself — display name, description, and profile picture — stored beside the
  persona on the memory root. The name and description are injected into every
  run as text; the picture is never force-fed each turn (that reads as user
  content and derails the model), but the context tells the model it *has* a
  picture it can look at or send on request via `view_profile_picture` /
  `send_profile_picture`. Managed from the admin dashboard (name/description +
  avatar upload, mime-validated, with optimistic-concurrency 409s) and served
  read-only to the chat UI so it can render the assistant's face and name.
- **Knowledge auto-injection into context.** Beyond the on-demand
  `search_knowledge` tool, the top-k corpus chunks most relevant to each message
  can now be folded straight into the run context. Gated by the new
  `knowledge_context_top_k` (0 = tool-only, the default); one knowledge store is
  built once and shared by the search tool and the context path. Retrieval is
  crash-proof — a failure never breaks a turn — and its contribution is surfaced
  in the `!ctx` accounting.
- **Operator-triggered memory passes.** Run the same session-summary fold,
  durable-memory curation, and session flush the chat path runs automatically —
  on demand for a chosen session, from the admin UI. The two model-backed passes
  report an honest capability status when no model is wired.
- **Per-turn token & context-window accounting.** Every reply now carries
  best-effort token usage (input/output/total/cached/reasoning) plus the lead's
  configured context window, serialized on the HTTP reply and the SSE `done`
  frame. The web chat console renders a live context-window meter.
- **Richer web chat rendering.** Syntax-highlighted code blocks (Shiki),
  `mermaid` fenced diagrams rendered as SVG (with graceful fallback to code), and
  voice dictation that surfaces mic/permission errors instead of swallowing them.
- **Chat transcript media offload.** Inline `data:` image/file payloads are moved
  out to the blob store and replaced with references, so persisted transcripts
  stay small.

### Fixed

- **Recent facts vanished on the semantic-retrieval path.** With semantic memory
  on and a curated fact sheet, freshly-`remember`-ed facts were dropped from
  context until the next curation pass folded them in — the recent-raw tail was
  only appended on the whole-file path. It's now appended by recency on both
  paths.
- **Evicted-but-unsummarized turns disappeared from context.** Turns pushed out
  of the live window into the pending buffer weren't rendered, so up to
  `summarize_every` of the *most recent* turns went invisible until a fold fired.
  Context now renders the pending buffer ahead of the live turns.

### Changed

- The admin app now shares the chat stack's live `MemoryManager` (and its on-disk
  view) instead of constructing a standalone store; standalone `main.py admin`
  still builds a model-free manager.
- Extracted a shared deterministic curation-apply path reused by both the
  per-turn curator and the new session-summary curation.
- The in-process admin surface is enabled by default in the bundled entrypoints.

### Docs

- Slimmed the root `README.md` to a feature- and screenshot-focused map, moving
  the verbose setup walkthroughs (Docker, Open WebUI, storage backends) into
  [`docs/`](docs/) and fixing stale references (`apply_deployment_config` →
  `configure_*`).
- Added this changelog.

## Earlier

Condensed from the commit history, newest first.

### Added

- Streaming chat console in the admin frontend, over the chat-API SSE stream.
- Frameless native desktop shell (PySide6 + QtWebEngine) that renders the web
  frontend and serves it from one process, with a JS↔Python bridge.
- Streamed reasoning/tool events on the HTTP API, plus inbound file attachments.
- Team introspection view in the admin dashboard (live roster snapshot).
- `magi.client` desktop SDK — embedded (in-process), HTTP, and blocking `Sync`
  fronts over one call surface.
- Admin dashboard rebuilt on the `@carneirofc/ui` design system with light/dark
  themes and a screenshot gallery.

### Changed

- Collapsed the per-channel `main_*.py` entrypoints into a single `main.py`.
