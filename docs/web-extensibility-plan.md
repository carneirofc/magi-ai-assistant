# Web extensibility implementation plan

_Status: implemented in-repo. Created 2026-07-10._

How to evolve the web frontend from a thin page/route re-export library into a
developer-first, code-first, composition-first frontend toolkit — while keeping
the in-repo Next app as the runnable starter overlay and proof that the library
is easy to extend.

This plan builds on [`frontend-split.md`](frontend-split.md): that doc extracted
`@carneirofc/magi-web` from the app and proved that routes, pages, middleware,
and components could ship from the library. This doc is the next step: make the
library easy for developers to extend **without forking**, and make the example
app teach that model clearly.

---

## Decisions (locked)

| Question | Decision |
|---|---|
| What is the primary goal? | **Make the web library easy for developers to extend.** |
| What extension model comes first? | **Overlay/composition first.** The consuming app stays a thin Next overlay that mounts and composes library building blocks. |
| Who is the target audience? | **Developers.** Optimize for code-level customization, not non-technical theme/config knobs. |
| What is the primary customization style? | **Code-first.** Config stays small/supporting; the real extension surface is code. |
| What should the example app be? | **A thin starter overlay** that demonstrates the customization seams explicitly. |
| How should the library be organized? | **Vertical feature slices** (`chat`, `knowledge`, `memory`, `identity`, `team`, etc.). |
| What is the real contract of a slice? | **Types + hooks + components.** |
| Are default screens/pages still useful? | **Yes, but only as convenience compositions** built on top of the real slice contract. |
| How open is the API surface? | **Open internals are allowed**, but the library must document **stability tiers** (`Stable`, `Advanced`, `Internal/Experimental`). |
| How should consumers discover slices? | **Explicit slice entrypoints** rather than one giant root export or undocumented deep paths. |
| How should route/server logic work? | The library may provide **reusable route helpers/default handlers**, but the **consuming app owns final route files**. |
| How opinionated should layout/nav be? | Ship a **default shell/nav**, but make it replaceable. |
| Who owns navigation? | The **app** assembles a **central typed nav config**, possibly using per-slice defaults. |
| Should slices contribute defaults? | **Yes**, but defaults are convenience metadata, not the core slice contract. |
| Should the library provide an app builder? | **Yes — a thin typed helper** with an easy escape hatch; the output must stay transparent/plain. |
| What is the main proof of success? | A developer can **replace one built-in page quickly** without editing library internals. |
| What is the golden-path customization example? | **Chat.** |
| What should “customize Chat” mean? | Replace the **screen composition** while reusing lower-level chat hooks/components/helpers. |
| Which chat seams must be stable first? | **Hooks + key sections + typed route/API helpers.** |
| How should chat state be exposed? | The library owns the default state model and exposes a **documented typed controller surface**. |
| How should chat rendering be customized? | **Section-level customization first**, with selective render overrides at lower-level section/component seams. |
| How much should the public surface mirror the underlying runtime? | Expose a **stable MAGI-specific façade**, not raw runtime details. |
| Which slice comes after Chat? | **Knowledge.** |
| Should the slice pattern be defined first? | **Yes.** Establish the shared contract before migrating slices. |
| How formal should the slice contract be? | **Types/interfaces + a small helper/factory layer.** |
| What should the helper layer optimize for? | **Reducing assembly boilerplate while keeping the output transparent.** |
| How aggressive should the migration be? | **Contained breaking reorganization now.** Keep only cheap/high-value compatibility shims. |
| What should the docs teach first? | **Slice building blocks first**, then default screens as shortcuts. |

---

## Problem statement

The current frontend split solved the **artifact** problem: `@carneirofc/magi-web`
exists, the app can consume it, and the app's `app/` tree is mostly thin route/
page re-exports. That is useful, but it still centers the library around a
mostly-finished app shape:

- `src/pages/*` are the obvious entrypoints.
- `src/routes/*` are the obvious server seams.
- components and helpers exist, but the library does not yet teach a strong,
  stable, developer-facing extension ladder per domain feature.

For an internal overlay that mostly rebrands and mounts defaults, that is enough.
For a developer-oriented library that is supposed to be **easily extendable**, it
is not enough.

The new target is:

> The library should feel like a **composable frontend toolkit with opinionated
> defaults**, not like a sealed app whose pages happen to be importable.

That means a developer should be able to:

1. import a feature slice,
2. use its typed models and helpers,
3. wire its controller hooks,
4. compose with its section-level UI building blocks,
5. and only optionally take a ready-made screen/page if they want the shortcut.

---

## Target architecture

### 1. Center the library on feature slices

The public mental model should shift from:

- `components/*`
- `pages/*`
- `routes/*`
- `lib/*`

to:

- `chat`
- `knowledge`
- `memory`
- `identity`
- `team`
- `shell`
- `auth`

Each slice may still be implemented across internal folders, but the *developer
entrypoint* should be slice-oriented.

### 2. Make the real contract “types + hooks + components”

For each slice, the primary supported ladder is:

1. **Types / API helpers** — typed contracts, transport helpers, route-helper
   glue where appropriate.
2. **Hooks / controller helpers** — the stateful API a developer builds on.
3. **Components / sections** — reusable feature UI pieces at a meaningful level.

Everything above that is secondary:

4. **Default screens/pages** — convenience compositions, not the center.
5. **Defaults bundles / app-builder integration** — assembly helpers, not the
   slice's defining contract.

### 3. Keep the example app thin, but make it educational

The in-repo Next app should remain the reference overlay, but it should teach the
extension model instead of merely consuming it.

It should explicitly demonstrate:

- mounting the default shell/nav,
- mounting default route/page conveniences,
- replacing the Chat screen composition locally,
- adding local app-owned nav items,
- and reusing slice-level hooks/components rather than copy-pasting a page.

### 4. Use stability tiers instead of pretending every file is equal

The library will stay hackable — deep imports are allowed — but the docs and
entrypoints must label what is actually intended to be depended on:

- **Stable** — intended extension surface; preserve aggressively.
- **Advanced** — usable and documented, but lower-level or more coupled.
- **Internal / Experimental** — importable, but may break during refactors.

This matches the developer audience better than either a locked-down public API
or a silent free-for-all.

---

## Proposed slice contract

The slice contract should be lightweight, explicit, and partly encoded in types.
It should not become a framework with hidden registration magic.

### Required per slice

Every first-class slice should expose:

1. **Typed domain/API surface**
   - public feature types
   - request/response shapes where owned by the library
   - feature-specific transport/helper functions where appropriate

2. **Controller hooks**
   - the main stateful hooks a developer composes with
   - derived selectors/helpers if needed
   - typed event/callback contracts where interaction matters

3. **Section-level components**
   - meaningful feature UI sections
   - not every tiny internal leaf component
   - enough to rebuild the default screen composition without reimplementing the whole feature

### Optional per slice

Each slice may also expose:

- a default `...Screen` / `...PageView`
- route handler helpers or mounted route logic
- slice nav defaults
- app-builder integration metadata
- lower-level advanced/internal building blocks

### Naming and entrypoint guidance

Each slice should converge on predictable subpaths, for example:

```text
@carneirofc/magi-web/chat
@carneirofc/magi-web/chat/types
@carneirofc/magi-web/chat/hooks
@carneirofc/magi-web/chat/components
@carneirofc/magi-web/chat/screens      # convenience layer
@carneirofc/magi-web/chat/routes       # convenience layer
```

The exact export map can vary, but the developer experience should be predictable.

---

## Chat as the proving slice

Chat is the highest-value proof because it exercises the full extension story:

- typed data/transport,
- stateful runtime/controller logic,
- composition-heavy UI,
- route helpers/BFF behavior,
- and significant product value when customized.

### Chat success criterion

The redesign succeeds when a developer can replace the built-in Chat page
composition in under ~30 minutes while reusing the stable slice surface.

### Stable chat surface to establish first

**Stable**:

- chat types / transport helpers
- a MAGI-specific chat controller hook or hook family
- documented selectors/actions/callbacks
- message/thread section(s)
- composer section(s)
- session/history section(s) where appropriate
- the minimal typed route/API helper surface needed to connect the screen

**Advanced**:

- selective render overrides for messages/attachments/composer actions
- lower-level adapter seams
- more granular subcomponents that are useful but still implementation-coupled

**Internal / Experimental**:

- wiring details tightly bound to the current third-party runtime
- unstable presentational leaf components
- internal glue that exists only to support the default screen

### Chat screen philosophy

The default Chat page should remain available, but as a convenience composition
built on top of the stable chat slice, not as the primary architecture.

---

## Knowledge as the second slice

Knowledge is the second proving slice because it validates that the pattern is not
Chat-specific.

It should exercise:

- typed domain models,
- list/detail/add flows,
- route helper boundaries,
- section-level composition,
- and a default convenience screen built from the real slice contract.

If Chat and Knowledge both fit the same pattern cleanly, the slice contract is
probably correct for the rest of the library.

---

## Shell, nav, and app assembly

The shell/app layer should support composition without becoming magical.

### Default shell

The library should continue to ship a default shell/nav experience, but it should
be clearly replaceable:

- consumers may keep the shell and override nav/copy,
- replace the shell wholesale,
- or keep the shell while swapping individual feature screens.

### Navigation model

Navigation should be **declared centrally by the app** using typed config, not
inferred from mounted routes or hidden registries.

Feature slices may contribute defaults, but the app remains the final authority
on:

- ordering,
- grouping,
- labels,
- visibility,
- and app-local entries.

### Thin app-builder helper

The library may provide a thin typed helper to reduce repeated app assembly code,
but the helper must return transparent/plain structures. A developer should be
able to read the result and bypass the helper when they want.

---

## Migration strategy

Use a **contained breaking reorganization now** rather than accreting more
structure around the existing export map.

### Why break now

Preserving every current path while introducing slice-first developer entrypoints
would likely produce a confused hybrid:

- old page/route-centric exports remain the de facto model,
- new slice entrypoints become aliases rather than the real center,
- and the docs cannot teach a single coherent extension story.

Since this package is still internal-ish and the refactor target is now clear, it
is cheaper to reorganize intentionally now.

### Compatibility policy

Keep only cheap/high-value shims, for example:

- re-exporting a few widely used page views,
- preserving a handful of old subpaths where trivial,
- documenting moved paths clearly.

Do **not** preserve legacy structure if it prevents a clean slice-first design.

---

## Phased implementation plan

### Phase 0 — inventory the current frontend surface

Before moving files, inventory what `magi-web` currently exports and what the
reference app currently mounts.

Deliverables:

- map current exports in `web/packages/magi-web/package.json`
- identify current Chat/Knowledge components, hooks, routes, pages, and helper files
- identify app files in `web/src/app` that are pure mounts vs policy-bearing wrappers
- list candidate compatibility shims

Exit criteria:

- a written export inventory exists,
- and the current Chat/Knowledge seams are understood before restructuring begins.

### Phase 1 — define the shared slice contract

Create the small typed/helper layer that encodes the pattern.

Deliverables:

- slice contract types/interfaces
- stability-tier guidance and doc language
- naming/export conventions for slice entrypoints
- a small helper/factory layer for defaults/app assembly (if useful)

Exit criteria:

- there is a canonical “how to build a slice” pattern in code,
- not just in prose.

### Phase 2 — refactor Chat into the slice model

Restructure Chat around the real contract: types, controller hooks, and section
components first; default screen second.

Deliverables:

- explicit Chat slice entrypoints
- documented typed chat controller surface
- section-level Chat component exports
- default Chat screen/page rebuilt on top of those exports
- route/API helper surface aligned to the new slice

Exit criteria:

- the reference app can mount the default Chat screen,
- and also mount a custom Chat composition built from slice building blocks.

### Phase 3 — refactor Knowledge into the slice model

Apply the same structure to Knowledge.

Deliverables:

- explicit Knowledge slice entrypoints
- reusable list/detail/add hooks/components
- convenience Knowledge screen/page on top

Exit criteria:

- the pattern generalizes cleanly beyond Chat.

### Phase 4 — reshape shell/nav/app assembly around the new model

Make the app-owned assembly seams clear.

Deliverables:

- typed nav config shape
- shell defaults plus documented replacement path
- thin app-builder helper if still justified after Chat/Knowledge work
- slice defaults contribution story (optional, convenience-only)

Exit criteria:

- the example app clearly owns nav and top-level composition,
- while still benefiting from defaults.

### Phase 5 — update the reference app into a true starter overlay

Turn the in-repo Next app into a more explicit teaching artifact.

Deliverables:

- preserve the thin mount files where appropriate,
- add at least one custom Chat composition in the app,
- demonstrate app-owned nav assembly,
- keep the app runnable as the reference experience.

Exit criteria:

- the app demonstrates both “use defaults” and “replace composition” clearly.

### Phase 6 — rewrite library/app docs around the real extension model

The docs should teach the actual architecture, not the historical accident.

Deliverables:

- update `web/packages/magi-web/README.md`
- update `web/README.md`
- update or supersede `docs/frontend-split.md`
- add a “customize Chat by composition” walkthrough

Exit criteria:

- docs teach slice building blocks first,
- default screens second.

---

## Verification / acceptance criteria

The refactor is successful when all of the following are true:

### Library structure

- there are explicit slice entrypoints for at least Chat and Knowledge
- the slice contract is discoverable and consistent
- stability tiers are documented

### Developer workflow

- a developer can import Chat types/hooks/components without reading internal file layout
- a developer can replace Chat screen composition without editing library internals
- the example app demonstrates that path directly

### App integration

- the app still owns final Next route files
- shell/nav assembly is explicit and app-owned
- defaults remain available, but are visibly optional

### Quality gate

- `npm run typecheck -w @carneirofc/magi-web`
- `npm run build` in `web/`
- any relevant frontend tests updated/passing

---

## Likely code moves

These are directional, not locked paths:

- extract or alias current Chat logic into slice-oriented exports
- extract or alias current Knowledge logic into slice-oriented exports
- preserve existing `pages/*` and `routes/*` as convenience entrypoints where cheap
- introduce new stable slice-oriented exports alongside or ahead of old paths
- move docs/examples to import from slice entrypoints first

The point is not merely to rename folders. The point is to make the *developer
mental model* line up with the actual architecture.

---

## Risks / tradeoffs

### Risk: overengineering a framework

If the helper/factory layer grows too much, the result will feel like hidden magic.
Mitigation: keep helpers thin; keep outputs transparent; prefer plain objects and
plain React composition.

### Risk: preserving too much compatibility

If old exports remain the easiest path, the new architecture will not take hold.
Mitigation: keep only cheap/high-value shims; teach new paths first.

### Risk: slice inconsistency

If Chat and Knowledge are refactored differently, the pattern will collapse.
Mitigation: define the slice contract first; migrate Chat and Knowledge against it.

### Risk: exposing runtime internals by accident

If the Chat public API mirrors the current underlying runtime too closely, future
refactors will be painful.
Mitigation: expose a MAGI-specific façade and label lower seams as Advanced or Internal.

---

## Immediate next steps

1. Inventory the current `magi-web` export map and identify candidate slice entrypoints.
2. Define the slice contract in code (types + small helper layer).
3. Design the Chat slice public surface before moving files.
4. Refactor the reference app so Chat proves the new extension model.
5. Repeat for Knowledge.

---

## Export inventory snapshot (implemented during refactor)

### Current export map before slice-first docs

`web/packages/magi-web/package.json` originally exported only these public subpaths:

- `@carneirofc/magi-web/components/*`
- `@carneirofc/magi-web/lib/*`
- `@carneirofc/magi-web/routes/*`
- `@carneirofc/magi-web/pages/*`
- `@carneirofc/magi-web/middleware`

That surface optimized for file-layout discovery, not developer task discovery. The slice refactor adds explicit entrypoints for:

- `@carneirofc/magi-web/slices/chat/*`
- `@carneirofc/magi-web/slices/knowledge/*`
- `@carneirofc/magi-web/slices/core`
- `@carneirofc/magi-web/slices/shell`

### Candidate feature slices identified from the current tree

- `chat` — `src/pages/chat.tsx`, `src/components/ChatConsole.tsx`, `src/lib/chat-*`, `src/routes/chat*`
- `knowledge` — `src/pages/knowledge*.tsx`, `src/components/KnowledgeList.tsx`, `AddKnowledge.tsx`, `Document*.tsx`
- `memory` — current pages/components remain page-oriented for now
- `identity`
- `subjects`
- `persona`
- `team`
- `shell`

### Initial seam classification

#### Stable candidates

- Chat: session types, controller helpers, `ChatConsole`, `ChatView`-level composition, typed route helpers
- Knowledge: list/document types, list/ingest/detail components, server fetch helpers for docs/subjects/tags
- Shell/nav: app-owned nav config plus `AppShell`

#### Advanced candidates

- Chat markdown/rendering helpers (`ContextDisplay`, code/mermaid render pieces)
- Knowledge document action/meta subcomponents
- Route helper re-exports for chat history/blob handling

#### Internal-only

- assistant-ui implementation details inside `ChatConsole`
- SSE frame parsing/offload internals
- page-copy utilities and low-level implementation helpers that are not feature-shaped

#### Legacy / compatibility-only

- `pages/chat`, `pages/knowledge*`
- `components/AppShell`, `components/Sidebar`
- file-layout-driven `components/*` and `lib/*` imports that now have slice equivalents

### Reference app mount inventory

Pure mounts before the refactor:

- `web/src/app/(app)/chat/page.tsx`
- `web/src/app/(app)/knowledge/page.tsx`
- `web/src/app/(app)/knowledge/add/page.tsx`
- `web/src/app/(app)/knowledge/[...docId]/page.tsx`
- most other `(app)` page files and `api/**` route files

Policy-bearing wrappers after the refactor:

- `web/src/app/(app)/layout.tsx` — explicit app-owned shell/nav assembly
- `web/src/app/(app)/chat/page.tsx` — custom Chat composition built from slice exports

---

## Non-goals (for this phase)

- building a runtime plugin ecosystem
- making configuration the primary extension model
- supporting multiple unrelated frontend frameworks
- freezing every internal component as a public API
- eliminating all convenience page/route exports

This phase is about making extension **clear, fast, and code-centric** for
developers using the existing Next/React stack.