# Project

magi is the **reusable core of a personal AI assistant** — one shared brain
(lead model routing to a specialist team, with deliberate memory, a knowledge
corpus, and tools) driven by several channels (Discord, HTTP, an OpenAI-compatible
shim, a desktop shell). A private *persona* overlay repo extends prompts, members,
and tools from the outside without editing this tree.

Two shippable products live here:
- The Python **engine** (`src/magi/`, distribution `magi-ai-assistant`, import
  package `magi`).
- The **web frontend** (`web/`): a Next.js operator admin/chat app (BFF) and the
  `@carneirofc/magi-web` React library it consumes.

Read [docs/architecture.md](docs/architecture.md) before non-trivial engine work;
[CONTEXT.md](CONTEXT.md) is the memory-subsystem glossary. `docs/` holds the deep
dives and ADRs; keep them current when the contracts they describe change.

# Verification

- Engine: `uv run ruff check src tests` and `uv run pytest -q`.
- Web (from `web/`): `npm run typecheck` and `npm run typecheck -w @carneirofc/magi-web`.
- `pre-commit` runs all four; CI (`.github/workflows/ci.yml`) enforces them.

# Git commits
- Always use [Conventional Commits](https://www.conventionalcommits.org/) for commit messages (e.g. `feat:`, `fix:`, `chore:`, `docs:`).
- Whenever a `CHANGELOG.md` file exists, add a matching changelog entry for the change. If no `CHANGELOG.md` exists, suggest creating one.
- Never include `Co-Authored-By` (or any co-author trailer) in commit messages.

# AGENTS.md hierarchy

- A performant AGENTS.md hierarchy is installed here
- Agents must follow these instructions across any edits

## Core Contract

- AGENTS.md files are binding work contracts for their subtrees
- Work products, source materials, instructions, records, assets, and durable docs must stay understandable from the nearest applicable AGENTS.md plus every parent AGENTS.md above it

## Read Before Editing

1. Read the root AGENTS.md
2. Identify every file or folder you expect to touch
3. Walk from the repository root to each target path
4. Read every AGENTS.md found along each route
5. If a parent AGENTS.md lists a child AGENTS.md whose scope contains the path, read that child and continue from there
6. Use the nearest AGENTS.md as the local contract and parent docs for repo-wide rules
7. If docs conflict, the closer doc controls local work details, but no child doc may weaken these rules

Do not rely on memory. Re-read the applicable AGENTS.md chain in the current session before editing.

## Update After Editing

Every meaningful change requires an AGENTS.md pass before the task is done.

Update the closest owning AGENTS.md when a change affects:

- purpose, scope, ownership, or responsibilities
- durable structure, contracts, workflows, or operating rules
- required inputs, outputs, permissions, constraints, side effects, or artifacts
- user preferences about behavior, communication, process, organization, or quality
- AGENTS.md creation, deletion, move, rename, or index contents

Update parent docs when parent-level structure, ownership, workflow, or child index changes. Update child docs when parent changes alter local rules. Remove stale or contradictory text immediately. Small edits that do not change behavior or contracts may leave docs unchanged, but the AGENTS.md pass still must happen.

## Hierarchy

- Root AGENTS.md is the rail: project-wide instructions, global preferences, durable workflow rules, and the top-level Child Index
- Child AGENTS.md files own domain-specific instructions and their own Child Index
- Each parent explains what its direct children cover and what stays owned by the parent
- The closer a doc is to the work, the more specific and practical it must be

## Child Doc Shape

- Create a child AGENTS.md when a folder becomes a durable boundary with its own purpose, rules, responsibilities, workflow, materials, or quality standards
- Work Guidance must reflect the current standards of the project or user instructions; if there are no specific standards or instructions yet, leave it empty
- Verification must reflect an existing check; if no verification framework exists yet, leave it empty and update it when one exists

Default section order:
- Purpose
- Ownership
- Local Contracts
- Work Guidance
- Verification
- Child Index

## Style

- Keep docs concise, current, and operational
- Document stable contracts, not diary entries
- Put broad rules in parent docs and concrete details in child docs
- Prefer direct bullets with explicit names
- Do not duplicate rules across many files unless each scope needs a local version
- Delete stale notes instead of explaining history
- Trim obvious statements, repeated rules, misplaced detail, and warnings for risks that no longer exist

## Closeout

1. Re-check changed paths against the AGENTS.md chain
2. Update nearest owning docs and any affected parents or children
3. Refresh every affected Child Index
4. Remove stale or contradictory text
5. Run existing verification when relevant
6. Report any docs intentionally left unchanged and why

## User Preferences

When the user requests a durable behavior change, record it here or in the relevant child AGENTS.md

## Child Index

- `src/magi/` — the Python engine. Layering, DI, and extension-point rules for the
  shared brain. Children:
  - `src/magi/core/` — model-free mechanism: conversation runner, config, memory,
    knowledge, storage, db, media.
  - `src/magi/agent/` — model-bound brain: team, members, model builders, curator,
    summarizers, and the tool registry.
  - `src/magi/channels/` — transport adapters (Discord, HTTP/API, admin) over the
    shared `PlatformAdapter` gateway.
- `tests/` — the engine's pytest suite and its code-first config conventions.
- `web/` — the frontend: Next.js admin/chat BFF plus the `@carneirofc/magi-web`
  library. Child:
  - `web/packages/magi-web/` — the presentational React + SSE-runtime library
    (slice architecture, generated API types).

Owned by root (no child doc): `main.py` (entrypoints/wiring), `docs/`, `clients/`,
`examples/`, `scripts/`, and deployment files (`Dockerfile`, `docker-compose*.yaml`,
`litellm.config.yaml`, `.github/`).
