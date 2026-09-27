# Changelog

All notable changes to **magi** are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/).

## [Unreleased]

### Added

- **Sandboxed shell commands** (`sandbox.backend: docker | local`). The lead's
  `run_command` runs in a per-user workspace — by default a hardened
  throwaway container (no network, read-only root, all caps dropped, resource
  limits). Only `sandbox.allowed_users` may use it; a policy refuses
  catastrophic commands and holds risky ones for the user's `/approve <id>`
  (or `/deny <id>`), which every channel supports. Every call is audited to
  `$MAGI_HOME/logs/exec.jsonl`; `magi doctor` checks the backend. See ADR 0007.

- **Session search** (`session_search_enabled`). Finished turns are indexed in
  a local SQLite FTS5 file and the lead gets `search_sessions`, always scoped
  to the current user. `magi doctor` checks FTS5.
- **Delegation** (`delegation_enabled`). `delegate_task` runs a self-contained
  subtask on an isolated helper agent (member model and default tools, no
  memory, no recursion) with its own timeout and tool-call cap.

- **File skills (SKILL.md).** Skills in the agentskills.io format under
  `$MAGI_HOME/skills` (plus `skills.dirs`) join the Python skill registry.
  Progressive disclosure: the lead sees `name: description`, `skill_view`
  loads the body and bundled files. The assistant can save and improve skills
  (`skill_create`, `skill_patch`) per `skills.agent_write` — `propose` routes
  through the evolution queue (new `skill` proposal kind), `direct` writes
  immediately. `magi skills list` and new doctor checks. See ADR 0006.
- **Toolsets.** The lead's tools are grouped (`http`, `media`, `websearch`,
  `skills`, …) and `toolsets.disabled` switches groups off.
- `magi.core.log` — typed `log_info` / `log_warning` for strict-mode code.

- **Bare-metal deploy.** `scripts/install.sh` installs magi with
  `uv tool install` (extras via `MAGI_EXTRAS`) and runs `magi setup`.
  `magi gateway install|uninstall|status|logs` manages a systemd user service
  that runs `magi run` and restarts on failure.

- **One process, many channels.** `magi run api discord telegram admin` (or
  `channels.enabled`) builds the brain once and serves every channel from it
  (`channels/registry.py`, ADR 0005). Adapters get the shared service through
  `ConversationService.with_guidance`; the API and admin servers stop
  gracefully (`gateway.Stoppable`, `run_gateway(on_first_exit=…)`). `api` and
  `admin` port collisions fail at startup.
- **Telegram channel** (`telegram` extra, python-telegram-bot). Long polling,
  deny-by-default allowlist (`telegram_allowed_users`, `/whoami` to find an
  id), photos/documents as media, 4096-char chunking, `/new` to reset. New
  secret `TELEGRAM_BOT_TOKEN`; `magi setup` and `magi doctor` know about it.

- **`magi` CLI and typed config file.** A validated YAML config
  (`./magi.yaml`, else `$MAGI_HOME/config.yaml`; `MAGI_HOME` defaults to
  `~/.magi`) replaces editing Python to deploy. New commands: `magi setup`
  (wizard; secrets go to `$MAGI_HOME/.env` with mode 0600), `magi run
  [channel]`, `magi doctor` (backend reachability, missing extras, tokens,
  FTS5), and `magi config path|show|get|set`. Relative data paths resolve
  against the config file's directory. New `channels.enabled` setting. See
  ADR 0004.

- **Static type checking (basedpyright).** `uv run basedpyright` now gates CI
  and pre-commit: `standard` mode everywhere, `strict` for the model-free
  `src/magi/core`. Pre-existing findings are frozen in
  `.basedpyright/baseline.json`; new code must be clean and a PR check
  rejects a baseline that grows. CI now syncs `--all-extras` so the lazily
  imported optional backends are typed.

- **Runtime validation of untrusted JSON (web).** Admin-api responses are now
  parsed with zod schemas generated from the OpenAPI spec
  (`src/lib/api-schemas.ts`, via `openapi-zod-client` in `npm run gen:api`;
  `OPENAPI_SPEC_PATH` generates from a local spec file). Chat-api responses,
  transcripts, and localStorage records use hand-written schemas
  (`lib/wire-schemas.ts`, `lib/runtime-config-schema.ts`), and BFF routes
  answer 400 on a malformed body (`lib/route-body.ts`). New `fetchJson` helper
  in `lib/utils` (`@carneirofc/magi-web` 0.9.1, adds `zod` as a dependency; 0.9.0 was tagged but
  never published).
- **Pydantic validation at the engine's JSON boundaries.** Settings, identity,
  subjects, memory windows and fact sheets, reminders, evolution proposals,
  storage sidecars, the STT sidecar, upstream tool APIs, the client SDK, and
  curator/mood LLM output are validated with pydantic. Bad input still
  degrades exactly as before. New `magi.core.types` (`JsonValue`,
  `JsonObject`, `parse_json_object`, `parse_json_array`).
- **Biome** lints and formats the web workspace (`npm run lint`,
  `npm run format`, `npm run check:fix`), replacing the unconfigured
  `next lint`.
- **Web operator auth is now optional.** When `ADMIN_PASSWORD` is unset or
  empty the BFF runs open: the auth middleware skips the session gate and the
  login route sends visitors straight into the app. There is no default
  password — an unset value means open, never a fallback secret. Setting
  `ADMIN_PASSWORD` restores the password → httpOnly-session flow unchanged
  (`@carneirofc/magi-web` 0.8.0, new `authEnabled()` in `lib/session`).

### Changed

- **One compose file, opt-in profiles.** `docker-compose.app.yaml` is merged
  into `docker-compose.yaml`; every service now sits behind a profile
  (`qdrant`, `s3`, `litellm`, `monitoring`, `app`, `admin`), so a bare
  `docker compose up` starts nothing. Qdrant, LiteLLM, and the admin API gained
  healthchecks. The container runs `magi run` with `docker/magi.docker.yaml`
  (one process for every enabled channel; the separate `discord` service is
  gone — list `discord` in `channels.enabled`). `MAGI_HOME` lives under
  `data/` in the image.

- **`main.py` is a thin wrapper.** The repo's deployment settings moved to
  `magi.yaml` and the `--docker` deltas to `docker/magi.docker.yaml`;
  `python main.py <channel> [--docker]` behaves as before. `pyyaml` is now a
  direct dependency.

- **Typed, validated config.** `Config` is now a frozen pydantic model:
  `configure(...)` checks value types as well as names (a failed call changes
  nothing), `model_provider`, `embeddings_provider`, and `storage_backend` are
  `Literal`s, and free-form blobs are `JsonObject`. New helpers: `derive()`
  (validated copy), `reset_config()`, `load_secrets(home)`, and
  `secret_fields()` (the masked-in-logs list is now derived from it). Tests
  restore the singleton automatically after each test.

- **Typed upstream parsing.** The Ollama tools validate `/api/tags`,
  `/api/show`, and `/api/ps` with pydantic wire models instead of `.get()`
  chains. MCP server specs are validated into an `McpServerSpec` model (a
  wrong-typed field now skips that server with a warning); the operator
  settings and admin MCP endpoints are typed `JsonObject`. The Discord reply
  target is typed as `Thread | DMChannel | TextChannel`, and the client's two
  `# type: ignore` comments are gone.

- **Stricter ruff rules, enforced format.** ruff now selects `E, F, I, UP, B,
  ANN`: annotations are required and `typing.Any` is banned (`ANN401`). The
  tree is reformatted with `ruff format`, and CI plus pre-commit gate
  `ruff check .`, `ruff format --check .`, and `biome ci .`.
- **`@carneirofc/magi-web` 0.9.1 moves to assistant-ui 0.15** (`@assistant-ui/react`
  ^0.15.22, `@assistant-ui/react-markdown` ^0.14.17). The removed legacy hooks
  are replaced with `useAui` / `useAuiState` in `ChatConsole` and
  `ArchiveReference`. `react-markdown` 0.14.8+ requires react ^0.15, so fresh
  installs (CI, publish) could no longer resolve the 0.14 pin.
- `typing.Any` is removed from the engine, replaced with precise types,
  `Protocol`s, and `JsonValue`. `HttpClient.context_stats()` now returns
  `JsonObject`.
- **`ChatConsole` absorbs the companion presence and reads like an LLM
  chatbot.** New optional props merge the two surfaces into one: `expressions`
  (mood → portrait URL) puts a mood-reactive bust in a new header bar, the full
  `PersonaStage` portrait in a collapsible presence column (`presencePanel`
  mounts extra cards under it, e.g. memory/reminders), and the persona's face
  on every reply avatar; `personaName` and `headerExtra` finish the header;
  `defaultUserId` seeds the memory-scoping user id while keeping the operator
  "chat as" switcher — now a compact header popover instead of an always-on
  input (`pinnedUserId` still hides it entirely). The transcript was restyled
  chatbot-first: a centered readable column, flat assistant messages,
  right-aligned user bubbles without sender avatars, and a persona-aware empty
  state and composer placeholder. All existing props and features (attachments,
  dictation, TTS, quoting, branches, context meter, session rail) are
  unchanged, and `CompanionSurface` still works for the side-stage arrangement.

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
