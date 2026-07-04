# Frontend split & distribution plan

_Status: implemented (initial cut). Created 2026-07-04._

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
| Presentational components (`components/*`) | Which pages/routes exist |
| Chat runtime (assistant-ui adapters, SSE, history/session/attachment/dictation) | Branding, theme tokens, copy |
| Typed admin API client (`lib/api-types.ts`, generated from the engine's OpenAPI) | Backend URLs, auth, cookies |
| Shared utils | The **BFF route handlers** (`app/api/*`) — env config + secrets live here |

**The rule that makes it a library, not a fork:** the library owns *zero*
policy — no hardcoded backend URL, no branding strings, no auth. All injected by
the consumer via props/config and the server-side BFF. If that holds, an overlay
is ~composition + a theme; if it doesn't, you've shipped a copy-paste.

---

## Topology

```
magi (this repo)                             persona (private overlay)
└── web/                                      └── web-overlay/            ← a thin Next app
    ├── package.json      workspace root          ├── package.json        deps: @carneirofc/magi-web
    │                     (magi-admin-web,             │                          @carneirofc/ui
    │                      the reference app)          ├── next.config.mjs transpilePackages: [...]
    ├── next.config.mjs   transpilePackages           ├── .npmrc          @carneirofc → GitHub Packages
    ├── src/app/          ← example pages + BFF        ├── app/            ← its pages (compose lib components)
    ├── src/middleware.ts                              │   ├── globals.css @source + its theme tokens
    └── packages/                                      │   └── api/        ← its BFF (backend URLs, auth)
        └── magi-web/     ← THE LIBRARY               └── ...
            ├── package.json   @carneirofc/magi-web
            ├── src/components/ ← presentational
            ├── src/lib/        ← chat runtime + API client
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

**4. Compose pages + honor the BFF route contract** — the overlay owns routes; it
imports mechanism from the lib and supplies policy through its own BFF handlers.
The chat runtime calls **fixed same-origin paths** (`/api/chat`,
`/api/chat/blobs/*`) rather than taking a backend URL — so the seam is a *route
contract*: the overlay implements those routes, and that's where the engine URL +
auth token live. The library never sees a backend URL.

```tsx
// web-overlay/app/chat/page.tsx
import { ChatConsole } from "@carneirofc/magi-web/components/ChatConsole";

export default function ChatPage() {
  // No props — the console streams against /api/chat, a BFF route THIS app owns.
  return <ChatConsole />;
}
```

```ts
// web-overlay/app/api/chat/route.ts — the overlay's BFF (policy)
// Proxy to the engine; inject CHAT_API_URL + auth here. Copy the reference
// app's src/app/api/chat/route.ts as the starting point.
```

### Personalization points, at a glance

| Want to change… | Where |
|---|---|
| Colors / fonts / accent | `globals.css` theme tokens (override `@carneirofc/ui`) |
| Which pages exist | the overlay's `app/` routes — import only the components you want |
| Backend URL / auth / secrets | the overlay's `app/api/*` BFF route handlers |
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
