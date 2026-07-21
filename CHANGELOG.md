# Changelog

All notable changes to **magi** are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/).

## [Unreleased]

## [0.4.0] - 2026-07-21

### Security

- **Both dependency audits are clean again.** Engine: `litellm` bumped past
  its yanked 1.87.0 pin to 1.93.0 (the first Python 3.14-compatible line —
  the yank had aborted `pip-audit` entirely), and the vulnerable transitive
  pins it then surfaced were patched (`aiohttp` 3.14.2, `pydantic-settings`
  2.14.2, `python-multipart` 0.0.32, `starlette` 1.3.1). Web: `postcss`
  (GHSA-qx2v-qp2m-jg93) and `js-yaml` (GHSA-52cp-r559-cp3m) are forced to
  patched versions via npm `overrides`, and `brace-expansion`
  (GHSA-3jxr-9vmj-r5cp) was patched in the lockfile. `pip-audit` and
  `npm audit` both report zero known vulnerabilities.

### Fixed

- **`@carneirofc/magi-web` publishes with a registry dependency again.** The
  library manifest shipped a local `file:` path for `@carneirofc/ui` (a
  sibling-checkout dev link), which broke CI publishing and would have been
  unresolvable for every consumer. The published manifest now declares the
  registry range; the local link lives only at the workspace root, and CI
  repoints it to the registry before installing.

## [0.3.0] - 2026-07-21

### Added

- **Capability roster grouped by origin.** The live team snapshot
  (`GET /v1/introspection`, rendered on the dashboard's team page) now reports
  *how* each lead capability got there: `builtin` (shipped with the engine),
  `skill` (skill manifest), `registered` (persona toolkit), `recipe`
  (operator-approved HTTP recipe), or `mcp`. Origins are stamped at team
  assembly — the view reflects what actually attached, not what config
  intended — and the team page groups the lead's tools under those headings,
  so the operator watches capability growth land as it happens.
- **The curator can file evolution proposals.** The post-turn memory curator
  gains an escalation path: when a behavioral issue clearly recurs and the fix
  belongs in an adjustable operating prompt rather than another persona line,
  it returns a `proposal` (target, replacement text, rationale) that is filed
  into the self-evolution queue as `source: "curator"` — same rails as the
  lead's propose tools (allowlist incl. registered skills, capped queue, human
  decision). Filing never breaks a chat: a full queue, bad target, or disabled
  evolution degrades to a log line, and the curation prompt contract documents
  when to propose vs. adjust the persona.
- **Per-user knowledge scope.** The knowledge store's reserved `scope` seam is
  now live end to end: `save_knowledge(personal=True)` files a document under
  the current user's own scope (`user:<id>`, resolved from the ambient memory
  scope — never a tool argument), and both `search_knowledge` and context
  auto-injection span the global corpus plus that user's scope, never anyone
  else's. The admin knowledge list exposes each document's scope, filters by it
  (`?scope=` on the API, a scope dropdown in the dashboard), and marks
  non-global documents in both table and card views.
- **Skill prompts are evolution targets.** Registered skills join the
  self-evolution allowlist (per-manifest opt-out via `Skill(proposable=False)`),
  so the assistant can propose improvements to its own skills under the same
  human-in-the-loop rails: queued, operator-decided, applied to the
  `prompts-runtime` overlay on approval — where it wins over the manifest's
  inline default at the next restart. The proposal's "current text" honestly
  shows the inline default when no overlay file exists yet; identity prompts
  stay non-proposable.
- **Skill manifests.** A skill — what the assistant *knows* plus what it *can
  do* — is now one registrable unit: `register_skill(Skill(name, prompt, tools,
  lead_toolkit, member_tools, enabled))`. At team build each active skill's
  prompt fragment is composed (labeled) into the lead's instructions and its
  tools attached, honoring the gate. The prompt is overlay-aware (`skills/
  <name>.md` wins over the inline default), a broken skill degrades with a
  warning instead of aborting boot, and registration is idempotent by name.
  Runnable demo: `examples/custom_skill.py`.
- **Tool registration seam for persona overlays.** The tool twin of
  `register_member`: `register_tool(fn)` appends a tool to the shared member
  default set (flows through `enabled_tools()`), and
  `register_lead_toolkit(builder)` registers a lead-level toolkit builder that
  receives the injected `MemoryManager` at team build — so a persona attaches
  code tools without editing the engine tree. Both are idempotent and
  decorator-usable; a raising lead toolkit is skipped with a warning instead of
  aborting startup.

- **Fixed-frame layout primitives.** `@carneirofc/magi-web` now ships `AppPage`
  (a freeform fill container) and `ScrollRegion` (the only element allowed to
  scroll, axis-aware), so a consuming app can behave like a native application
  window: the frame is viewport-sized and never grows a scrollbar, with all
  scrolling delegated to explicit inner regions. The shared page views
  (dashboard, chat, memory, knowledge, identity, persona, settings, subjects,
  team, evolution) are migrated onto them; a fill page is safe under both a
  fixed and a still-column-scroll frame.
- **Configurable desktop window minimum size.** `desktop_window_min_width` /
  `desktop_window_min_height` (default 320×380, the previous hardcoded floor) let
  a fixed-frame, desktop-only app floor the shell at its supported width so it
  never shrinks into a "window too small" state.

## [0.2.0] - 2026-07-10

### Added

- **Web slice entrypoints for extensibility.** `@carneirofc/magi-web` now exposes
  explicit slice-first entrypoints for Chat, Knowledge, shell/nav assembly, and a
  shared slice contract (`slices/chat/*`, `slices/knowledge/*`, `slices/shell`,
  `slices/core`) so consumers can customize by composition instead of file-hunting.
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

- **Reference web app now demonstrates app-owned composition.** The in-repo Next
  app keeps shell/nav assembly in the app and replaces the built-in Chat page with
  an app-local composition built from stable Chat slice exports.
- The admin app now shares the chat stack's live `MemoryManager` (and its on-disk
  view) instead of constructing a standalone store; standalone `main.py admin`
  still builds a model-free manager.
- Extracted a shared deterministic curation-apply path reused by both the
  per-turn curator and the new session-summary curation.
- Deepened the admin memory architecture: a dedicated `MemoryAdmin` module now
  owns operator memory reads/writes, session snapshots, trigger capability
  checks, optimistic concurrency, and semantic/archive reconciliation, leaving
  `channels/admin.py` as a thinner HTTP adapter over that interface.
- The in-process admin surface is enabled by default in the bundled entrypoints.

### Docs

- Web extensibility docs now teach building blocks first: the web extensibility
  plan, issue breakdown, library README, app README, and frontend split docs now
  lead with slice entrypoints and stability tiers, with convenience screens
  treated as secondary compatibility/convenience layers.
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
