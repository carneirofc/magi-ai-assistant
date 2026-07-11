# Frontend split & distribution plan

> Superseded as the primary teaching doc by [`web-extensibility-plan.md`](web-extensibility-plan.md).
> This document remains useful as the historical extraction/distribution record.

_Status: implemented. Created 2026-07-04; extended to ship routes + pages +
middleware from the library (app is now thin re-exports)._

The frontend twin of [`split-plan.md`](split-plan.md). That doc split the Python
side into a public **`magi` engine** (mechanism) and a private **persona
overlay** (policy). This doc does the same one layer up for the web frontend:
extract a reusable **`@carneirofc/magi-web`** library from the admin app, so a
persona overlay can build its own UI on the shared components + chat runtime.

---

## Decisions (locked)

| Question | Decision |
|---|---|
| Who consumes the UI library? | **Your own persona overlays.** Internal reuse — no public-API/semver rigor yet. |
| One artifact or several? | **Separate artifacts** — `@carneirofc/magi-web` (npm) and `magi` (pip), versioned independently. Mirrors the Python split. |
| Why is the npm scope `@carneirofc`, not `@magi`? | GitHub Packages binds the **scope to the repo owner**. "magi" lives in the package *name*, not the scope. A `@magi` scope would need a `magi` GitHub org — not worth it for internal reuse. |
| Ship a bundle or source? | **Source** (`.ts`/`.tsx`), consumed via Next `transpilePackages`. The library mixes client components and server utilities (`fs`/`crypto`); a pre-bundle would flatten the RSC `"use client"` boundary. |

---

## The seam: mechanism vs policy

Same discipline that keeps `core/` persona-neutral on the Python side:

| Library (`@carneirofc/magi-web`) — mechanism | App / overlay — policy |
|---|---|
| Presentational components (`components/*`) | Which routes/pages to mount (the thin `app/` tree) |
| Chat runtime (assistant-ui adapters, SSE, history/session/attachment/dictation) | Branding, theme tokens, page copy (overridable props) |
| Typed admin API client (`lib/api-types.ts`, generated from the engine's OpenAPI) | Backend URLs, auth, secrets — read from **env** by the lib clients |
| Shared utils | Route-segment config literals (`dynamic`/`runtime`) + middleware `matcher` |
| **BFF route handlers** (`routes/*`) — the same-origin proxy logic | The `.env` that feeds them (`ADMIN_API_URL`, `*_AUTH_TOKEN`, `ADMIN_PASSWORD`, `SESSION_SECRET`, …) |
| **Auth middleware** (`middleware`) + **page views** (`pages/*`) | Root `layout.tsx`, `globals.css`, `next.config.mjs` |

**The rule that makes it a library, not a fork:** the library owns *zero*
policy — no hardcoded backend URL, no branding strings, no auth. It reads config
from **env** (never `getenv` of anything but secrets/URLs) and takes every copy/
brand string as an **overridable prop defaulted to the reference text**. So the
routes and page views ship in the library as *mechanism*; the app supplies the
env, the theme, and the choice of which of them to mount. If that holds, an
overlay is ~composition + a theme; if it doesn't, you've shipped a copy-paste.

> **History:** the initial cut kept the BFF routes, pages, and copy on the app
> side. They were then extracted into the library — they held no policy (secrets
> live in the lib clients, read from env; copy is generic and now prop-overridable),
> so an overlay no longer copy-pastes ~1400 lines of plumbing. The app's `app/`
> tree is now thin **re-export files** that mount library `routes/*` + `pages/*`
> and declare the route-segment config Next reads statically.

---

## Topology

```
magi (this repo)                             persona (private overlay)
└── web/                                      └── web-overlay/            ← a thin Next app
    ├── package.json      workspace root          ├── package.json        deps: @carneirofc/magi-web
    │                     (magi-admin-web,             │                          @carneirofc/ui
    │                      the reference app)          ├── next.config.mjs transpilePackages: [...]
    ├── next.config.mjs   transpilePackages           ├── .npmrc          @carneirofc → GitHub Packages
    ├── src/app/          ← thin re-exports that       ├── app/            ← mounts lib routes + pages
    │                       mount lib routes/pages      │   ├── globals.css @source + its theme tokens
    ├── src/middleware.ts ← re-exports lib mw + matcher │   └── .env         backend URLs, auth (policy)
    ├── .env              ← backend URLs, auth (policy) └── ...
    └── packages/
        └── magi-web/     ← THE LIBRARY
            ├── package.json   @carneirofc/magi-web
            ├── src/components/ ← presentational
            ├── src/routes/     ← BFF handler logic (proxy the engine)
            ├── src/pages/      ← page views (copy via props)
            ├── src/middleware.ts ← auth gate
            ├── src/lib/        ← chat runtime + API client (read env)
            └── scripts/gen-api.mjs
```

The in-repo `web/` root is both the **workspace root** and the **reference app**
— it boots and chats out of the box (the frontend analog of the neutral demo
persona). `packages/magi-web` is the sole workspace member today.

---

## Distribution

**Registry:** GitHub Packages, `@carneirofc` scope. Auth (even for reads) needs a
PAT with `read:packages` as `NODE_AUTH_TOKEN`; publishing needs `write:packages`.
The `gh` CLI token does **not** carry these scopes.

**Publish:** bump `packages/magi-web/package.json` `version`, then tag:

```
git tag magi-web-v0.1.0
git push origin magi-web-v0.1.0
```

[`.github/workflows/publish-magi-web.yml`](../.github/workflows/publish-magi-web.yml)
installs the workspace, typechecks the library, and runs
`npm publish -w @carneirofc/magi-web` with the job's `GITHUB_TOKEN`
(`packages: write`). GitHub Packages rejects republishing an existing version, so
the version bump is the release gate.

**Local dev (no publish):** in this repo the app consumes the library through the
workspace symlink — edit a component, the app hot-reloads. This is the JS analog
of the engine's `uv` editable path source. You only publish when an overlay needs
a pinned release.

---

## Consuming it from a persona overlay (worked example)

An overlay is a Next.js app that installs `@carneirofc/magi-web`. Four seams:

**1. `.npmrc`** — resolve the scope from GitHub Packages:

```
@carneirofc:registry=https://npm.pkg.github.com
//npm.pkg.github.com/:_authToken=${NODE_AUTH_TOKEN}
```

**2. `next.config.mjs`** — compile the source package:

```js
const nextConfig = { transpilePackages: ["@carneirofc/magi-web"] };
export default nextConfig;
```

**3. `app/globals.css`** — Tailwind must scan the library, and this is where
**branding** happens (override `@carneirofc/ui` theme tokens):

```css
@import "tailwindcss";
@import "@carneirofc/ui/styles.css";
@source "../node_modules/@carneirofc/magi-web/src";
@source "../node_modules/@carneirofc/ui/dist";

/* personalize: override design tokens for this persona */
:root { --brand: /* the persona's accent */; }
```

**4. Mount routes + pages; set env** — the overlay owns *which* routes/pages exist
and the `.env` behind them; the library ships the handler logic and views. Each
`app/` file is a thin re-export. Next reads route-segment config (`dynamic`/
`runtime`) and the middleware `matcher` statically from the file at the route
path, so those literals stay in the overlay's files while the logic comes from the
library.

```ts
// web-overlay/app/api/chat/route.ts — mount the library's BFF handler
export { POST } from "@carneirofc/magi-web/routes/chat";
export const runtime = "nodejs";
export const dynamic = "force-dynamic";
// The engine URL + auth token come from THIS app's .env (CHAT_API_URL,
// API_AUTH_TOKEN) — the lib client reads them; the library never hardcodes a URL.
```

```tsx
// web-overlay/app/chat/page.tsx — mount the library's page view
export { default } from "@carneirofc/magi-web/pages/chat";
export const dynamic = "force-dynamic";
// Legacy compatibility path: still works, but prefer slice-first imports.
// To reskin the header, import { ChatView } and render <ChatView copy={{ title: … }} />.
```

```ts
// web-overlay/middleware.ts — mount the auth gate, own the matcher
export { middleware } from "@carneirofc/magi-web/middleware";
export const config = { matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"] };
```

An overlay that wants a bespoke page composes the library's `…View` (or raw
`components/*`) alongside its own components, and reskins the shell via
`<AppShell brand="…" nav={…} />`.

### Personalization points, at a glance

| Want to change… | Where |
|---|---|
| Colors / fonts / accent | `globals.css` theme tokens (override `@carneirofc/ui`) |
| Brand wordmark / logo / nav | `<AppShell brand tagline logo nav>` props (default to the reference values) |
| Page copy (titles/descriptions) | the `…View` `copy` prop, or write a bespoke `app/` page |
| Which routes/pages exist | the overlay's thin `app/` tree — mount only what you want |
| Backend URL / auth / secrets | the overlay's `.env` (the lib clients read it); never hardcoded |
| Add a bespoke widget | a component in the overlay, alongside imported ones |
| The engine contract (types) | pin a `@carneirofc/magi-web` version built against that engine |

Anything not on this list is mechanism — it belongs in the library, not the
overlay.

---

## Verify (needs the GitHub Packages token — do this once)

The refactor moved files and rewired imports; confirm it builds:

```
cd web
export NODE_AUTH_TOKEN=ghp_...          # PAT with read:packages
npm install                              # regenerates the workspace lockfile
npm run gen:api                          # optional: refresh the API client
npm run typecheck -w @carneirofc/magi-web
npm run build                            # next build of the reference app
```

Then `npm run dev` and click through chat / memory / knowledge / team.
