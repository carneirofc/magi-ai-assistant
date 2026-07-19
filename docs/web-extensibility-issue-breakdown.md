# Web extensibility issue breakdown

_Status: implemented in-repo. Created 2026-07-10._

## Execution status

- [x] Issue 1 — inventory captured in `docs/web-extensibility-plan.md`
- [x] Issue 2 — shared slice contract added in `web/packages/magi-web/src/slices/core.ts`
- [x] Issue 3 — stable Chat public surface defined via `src/slices/chat/*`
- [x] Issue 4 — Chat refactored into slice entrypoints with convenience screen preserved
- [x] Issue 5 — reference app `/chat` now uses custom app-owned composition
- [x] Issue 6 — Knowledge refactored into slice entrypoints with convenience screens preserved
- [x] Issue 7 — shell/nav/app assembly made explicit via `slices/shell`
- [x] Issue 8 — cheap compatibility shims preserved through existing `pages/*`/`components/*` exports
- [x] Issue 9 — docs rewritten to teach slice entrypoints first
- [x] Issue 10 — final verification and cleanup (library typecheck green; app build green)

Checklist breakdown of [`web-extensibility-plan.md`](web-extensibility-plan.md)
into issue-sized work items for the `@carneirofc/magi-web` / `web` refactor.

This is not a second architecture doc. It is the execution view: a prioritized
set of concrete tickets that can be created in GitHub Issues and worked in order.

---

## Epic

**Epic title:** Make `@carneirofc/magi-web` developer-first and easily extendable

**Epic outcome:**

- the library is organized around developer-facing feature slices,
- Chat proves the customization story,
- Knowledge proves the pattern generalizes,
- the reference app becomes a thin starter overlay that demonstrates the new seams,
- and the docs teach building blocks first, convenience screens second.

---

## Recommended issue order

1. Inventory current frontend export surface
2. Define the shared slice contract
3. Design stable Chat public surface
4. Refactor Chat into the slice model
5. Add custom Chat composition to the reference app
6. Design/refactor Knowledge into the slice model
7. Reshape shell/nav/app assembly seams
8. Add compatibility shims for high-value legacy imports
9. Rewrite library/app docs around the new model
10. Final verification and cleanup

---

## Issue 1 — Inventory current frontend export surface

**Goal**

Produce a written inventory of the current `magi-web` export map and the current
reference app mount points before any reorganization begins.

**Why this exists**

The planned refactor is intentionally breaking in places. We need a precise map
of what exists now so we can choose the new slice entrypoints deliberately and
preserve only the compatibility shims that are actually worth keeping.

**Scope**

- inspect `web/packages/magi-web/package.json` exports
- inspect `web/packages/magi-web/src/{components,lib,pages,routes}`
- inspect `web/src/app/**` mount files
- classify current seams as:
  - stable candidate
  - advanced candidate
  - internal-only
  - legacy/compatibility-only

**Deliverables**

- export inventory doc or section added to the plan
- list of candidate feature slices
- list of likely compatibility shims
- list of app files that are pure mounts vs policy-bearing wrappers

**Dependencies**

- none

**Acceptance criteria**

- there is a reviewed inventory of the current export surface
- Chat and Knowledge current files are identified clearly
- the team can name the first stable slice entrypoints before moving files

**Checklist**

- [x] Map current `package.json` export subpaths
- [x] Map current Chat files
- [x] Map current Knowledge files
- [x] Map current shell/nav files
- [x] Map current app mount files
- [x] Propose initial stable/advanced/internal classifications

---

## Issue 2 — Define the shared slice contract

**Goal**

Create the lightweight typed contract and helper layer that every feature slice
will follow.

**Why this exists**

Without a defined contract, Chat and Knowledge will drift into two different
patterns and the library will stay hard to teach.

**Scope**

- define the slice concept in code
- define naming/export conventions
- define stability-tier language
- add any thin helper/factory utilities that reduce boilerplate without hiding the result

**Deliverables**

- slice contract types/interfaces
- conventions for `types`, `hooks`, `components`, and optional `screens/routes`
- stability tier docs/comments
- optional helper utilities for slice/default assembly

**Dependencies**

- Issue 1

**Acceptance criteria**

- there is a canonical slice contract in code
- the contract enforces or strongly guides `types + hooks + components`
- the helper layer stays transparent/plain

**Checklist**

- [x] Define slice contract TypeScript types/interfaces
- [x] Define entrypoint naming conventions
- [x] Define stability-tier taxonomy and wording
- [x] Add helper/factory utilities if they remove real boilerplate
- [x] Document the contract where future contributors will see it

---

## Issue 3 — Design the stable Chat public surface

**Goal**

Specify the Chat slice public API before physically refactoring it.

**Why this exists**

Chat is the golden-path extensibility proof. Its stable surface must be chosen
carefully so the library exports MAGI concepts rather than leaking runtime internals.

**Scope**

- define Chat stable types
- define Chat controller hooks/selectors/actions/callbacks
- define the section-level component surface
- define which route/API helpers are stable vs advanced
- define which current internals remain internal/experimental

**Deliverables**

- Chat slice API proposal
- stability classification for Chat exports
- migration notes from current imports to new ones

**Dependencies**

- Issue 2

**Acceptance criteria**

- there is a clear stable Chat façade
- the façade is MAGI-specific, not raw runtime leakage
- a custom Chat screen can be sketched using only stable/advanced seams

**Checklist**

- [x] Identify stable Chat types/helpers
- [x] Identify stable controller hooks and actions
- [x] Identify stable section-level components
- [x] Mark advanced Chat seams
- [x] Mark internal-only Chat seams
- [x] Write migration/import guidance

---

## Issue 4 — Refactor Chat into the slice model

**Goal**

Implement the Chat slice so the real contract is `types + hooks + components`,
with the default Chat screen rebuilt on top as a convenience composition.

**Why this exists**

This is the main proof that the architecture is real.

**Scope**

- create Chat slice entrypoints
- reorganize Chat internals behind the new public surface
- keep or add convenience Chat screen exports
- keep route/API helpers aligned with the new slice

**Deliverables**

- stable Chat entrypoints
- stable Chat controller surface
- stable section-level Chat components
- default Chat screen built from those pieces
- updated imports in the reference app/library internals

**Dependencies**

- Issue 3

**Acceptance criteria**

- developers can import Chat from slice entrypoints instead of file-hunting
- the default Chat screen still works
- the library can support a custom Chat composition without editing internals

**Checklist**

- [x] Create Chat slice export structure
- [x] Move/alias Chat types to slice exports
- [x] Move/alias Chat hooks/controller to slice exports
- [x] Move/alias Chat sections/components to slice exports
- [x] Rebuild default Chat screen on top of the slice contract
- [x] Update internal imports to the new structure
- [x] Typecheck the package and app

---

## Issue 5 — Add custom Chat composition to the reference app

**Goal**

Use the in-repo Next app to demonstrate the golden-path customization story:
replace the built-in Chat page composition locally while reusing the stable Chat slice.

**Why this exists**

The architecture is only persuasive if the example app demonstrates it directly.

**Scope**

- create an app-local Chat page composition
- use stable Chat hooks/components/helpers
- keep the route ownership in the app
- document what is default vs customized

**Deliverables**

- custom Chat page in `web/src/app`
- continued support for the default convenience screen where useful
- code comments/docs showing what changed and why

**Dependencies**

- Issue 4

**Acceptance criteria**

- a developer reading the app can see how to replace the Chat screen composition
- the custom Chat page does not depend on library internals beyond declared tiers
- the app still builds and behaves correctly

**Checklist**

- [x] Decide what the custom Chat composition demonstrates
- [x] Build the custom page from slice exports
- [x] Preserve route ownership in the app
- [x] Verify no forbidden/internal imports creep in accidentally
- [x] Build/typecheck the app

---

## Issue 6 — Design and refactor Knowledge into the slice model

**Goal**

Apply the same slice architecture to Knowledge so the pattern is not Chat-specific.

**Why this exists**

One slice can always be a special case. Two slices prove the contract is reusable.

**Scope**

- identify Knowledge stable types/hooks/components
- create Knowledge slice entrypoints
- keep convenience screens on top of the real contract
- update imports and reference app mounts as needed

**Deliverables**

- stable Knowledge slice entrypoints
- section-level Knowledge components
- reusable Knowledge hooks/helpers
- convenience Knowledge screen(s)

**Dependencies**

- Issue 2
- preferably Issue 4 for pattern reuse

**Acceptance criteria**

- Knowledge fits the same contract cleanly
- the docs/example can point to both Chat and Knowledge as slice examples
- package/app typechecks still pass

**Checklist**

- [x] Identify Knowledge stable types/helpers
- [x] Identify Knowledge hooks
- [x] Identify Knowledge components/sections
- [x] Create Knowledge slice exports
- [x] Rebuild convenience screen(s) on top
- [x] Update imports and typecheck

---

## Issue 7 — Reshape shell/nav/app assembly seams

**Goal**

Make shell, navigation, and app-level assembly explicit, typed, and app-owned.

**Why this exists**

The library should provide defaults, but the consuming app must visibly own the
top-level composition and nav decisions.

**Scope**

- define typed nav config shape
- clarify shell replacement/composition seams
- add or refine thin app-builder/default helpers only if they reduce real boilerplate
- keep output transparent

**Deliverables**

- typed nav config
- shell/default nav composition guidance
- optional helper(s) for assembling app defaults

**Dependencies**

- Issue 2
- informed by Issues 4 and 6

**Acceptance criteria**

- the app owns nav ordering/grouping/labels/visibility explicitly
- the shell remains replaceable
- helpers, if any, do not hide the resulting structure

**Checklist**

- [x] Define nav item types
- [x] Define slice nav contribution shape if needed
- [x] Update shell APIs as needed
- [x] Add/apply thin assembly helper only if justified
- [x] Update reference app to use explicit app-owned nav config

---

## Issue 8 — Add compatibility shims for high-value legacy imports

**Goal**

Preserve only the old entrypoints that are cheap and high-value enough to keep
while the new slice model becomes the primary story.

**Why this exists**

The migration is intentionally breaking, but a few compatibility shims may make
the transition much smoother.

**Scope**

- identify legacy imports worth preserving
- add re-export shims where trivial
- avoid preserving old structure if it fights the new model

**Deliverables**

- compatibility shim list
- selected re-export paths
- migration notes for removed/renamed imports

**Dependencies**

- Issue 1
- after Issues 4/6 define the new primary structure

**Acceptance criteria**

- compatibility shims exist only where they help materially
- the new docs/examples still lead with slice entrypoints
- maintenance burden stays low

**Checklist**

- [x] List candidate old imports
- [x] Keep only cheap/high-value ones
- [x] Add shims/re-exports
- [x] Document removed paths and replacements

---

## Issue 9 — Rewrite library/app docs around the new model

**Goal**

Update the docs so they teach the actual architecture: building blocks first,
convenience screens second.

**Why this exists**

If the docs still teach page-level mounts first, the old mental model will remain.

**Scope**

- update `web/packages/magi-web/README.md`
- update `web/README.md`
- update or supersede `docs/frontend-split.md`
- add a Chat customization walkthrough

**Deliverables**

- revised library README
- revised app README
- updated architecture/frontend plan docs
- example code snippets using slice entrypoints

**Dependencies**

- Issues 4, 5, 6, and 7

**Acceptance criteria**

- docs start from slice entrypoints and building blocks
- docs clearly explain stability tiers
- the custom Chat composition path is documented

**Checklist**

- [x] Rewrite library README usage examples
- [x] Rewrite example app README to position it as a starter overlay
- [x] Update frontend architecture/planning docs
- [x] Add “customize Chat by composition” walkthrough
- [x] Verify all snippets match actual exports

---

## Issue 10 — Final verification and cleanup

**Goal**

Run the final validation pass, remove dead code/obsolete docs, and confirm the
refactor meets the acceptance criteria.

**Why this exists**

Large restructures often leave stale exports, stale docs, and unused helpers.

**Scope**

- typecheck/build validation
- test updates if relevant
- dead-code cleanup
- final acceptance review against the plan

**Deliverables**

- green typecheck/build
- cleaned-up stale files/imports/docs
- final acceptance checklist result

**Dependencies**

- Issues 4 through 9

**Acceptance criteria**

- `npm run typecheck -w @carneirofc/magi-web` passes
- `npm run build` in `web/` passes
- stale exports/docs are removed or updated
- acceptance criteria from `web-extensibility-plan.md` are satisfied

**Checklist**

- [x] Run library typecheck
- [x] Run app build
- [x] Run relevant tests
- [x] Remove stale imports/files/docs
- [x] Compare final result against the implementation plan

---

## Suggested labels

Use the repo's existing label vocabulary where helpful:

- `needs-triage` — newly created from this breakdown
- `ready-for-agent` — once scoped and unblocked
- `needs-info` — if a ticket requires an unresolved design choice

If you use milestones/projects, a single milestone such as **web extensibility**
would fit this breakdown well.

---

## Suggested first batch to create

If creating issues incrementally, start with these three:

1. **Inventory current frontend export surface**
2. **Define the shared slice contract**
3. **Design the stable Chat public surface**

Those three de-risk the rest of the effort and give implementation tickets a
clear target.