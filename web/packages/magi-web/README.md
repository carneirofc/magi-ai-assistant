# @carneirofc/magi-web

The **MAGI frontend library**: presentational React components and the SSE chat
runtime the admin/chat UI is built from. The frontend twin of the `magi` Python
engine — *mechanism* (components, chat runtime, typed API client) lives here and
is reused; *policy* (branding, theme, which pages exist, backend URLs, auth)
stays in the consuming app.

Published to **GitHub Packages** under `@carneirofc` (the scope is bound to the
repo owner; "magi" lives in the package name). Consumed by the in-repo
`magi-admin-web` reference app and by private persona overlays.

---

## Install

The consumer is a Next.js App-Router app. GitHub Packages needs auth even to read.

**1. Authenticate** — put this in the consumer's `.npmrc` (or your global
`~/.npmrc`, see the repo's `docs/frontend-split.md`):

```
@carneirofc:registry=https://npm.pkg.github.com
//npm.pkg.github.com/:_authToken=${NODE_AUTH_TOKEN}
```

Set `NODE_AUTH_TOKEN` to a classic PAT with `read:packages`, then:

```bash
npm install @carneirofc/magi-web
```

**2. Transpile the source package** — `next.config.mjs`:

```js
const nextConfig = { transpilePackages: ["@carneirofc/magi-web"] };
export default nextConfig;
```

**3. Let Tailwind scan it** — the components use Tailwind v4 + `@carneirofc/ui`
tokens. In `globals.css`:

```css
@import "tailwindcss";
@import "@carneirofc/ui/styles.css";
@source "../node_modules/@carneirofc/magi-web/src";
@source "../node_modules/@carneirofc/ui/dist";
```

> Ships **TypeScript source**, not a bundle — it mixes client components
> (`"use client"`) and server-only utilities (`fs`/`crypto`), so the consumer's
> Next build compiles it and resolves the RSC boundary. That's why steps 2–3 are
> required. There is no build/`dist` step.

## Use

Import by explicit slice entrypoint first. The library still ships components,
route helpers, convenience screens, and the auth middleware, but the primary
mental model is now:

- **stable slice contract:** `types + hooks + components`
- **advanced seams:** lower-level composition helpers and route helpers
- **legacy compatibility:** old `pages/*` and `components/*` imports kept only where cheap

### Slice entrypoints

```ts
import { ChatConsole } from "@carneirofc/magi-web/slices/chat/components";
import { chatCopy } from "@carneirofc/magi-web/slices/chat/screens";
import { getChatHealth } from "@carneirofc/magi-web/lib/chat-api";
import { KnowledgeList } from "@carneirofc/magi-web/slices/knowledge/components";
import { UserGrid, MemoryTabs } from "@carneirofc/magi-web/slices/memory/components";
import { listUsers, getProfile } from "@carneirofc/magi-web/slices/memory/hooks";
import { buildDefaultAppShellConfig, MagiAppShell } from "@carneirofc/magi-web/slices/shell";
```

### Stability tiers

- **Stable** — developer-first imports we teach and intend to preserve across routine refactors
- **Advanced** — lower-level supported seams for custom composition
- **Internal/Experimental** — contributor-facing implementation details
- **Legacy/Compatibility-only** — old paths kept as temporary re-exports

### Convenience screens still exist

You can still mount convenience screens from `pages/*`, but they are now the
secondary story. The first story is app-owned composition built from slice
entrypoints.

```ts
// app/api/chat/route.ts — mount the BFF handler (logic in the lib)
export { POST } from "@carneirofc/magi-web/routes/chat";
export const runtime = "nodejs";
export const dynamic = "force-dynamic";
```

```tsx
// app/(app)/chat/page.tsx — own the page, reuse the stable Chat slice
import { PageHeader, StatusBadge, StatusMessage } from "@carneirofc/ui";
import { ChatConsole } from "@carneirofc/magi-web/slices/chat/components";
import { chatCopy } from "@carneirofc/magi-web/slices/chat/screens";
import { getChatHealth } from "@carneirofc/magi-web/lib/chat-api";

export const dynamic = "force-dynamic";

export default async function ChatPage() {
  const health = await getChatHealth();
  return (
    <>
      <PageHeader
        subtitle={chatCopy.subtitle}
        title={chatCopy.title}
        description="App-owned composition using stable Chat slice parts."
        pills={<StatusBadge tone={health ? "success" : "error"}>{health ? "Chat API online" : "Chat API offline"}</StatusBadge>}
      />
      {health ? <ChatConsole /> : <StatusMessage tone="error">Chat API unavailable.</StatusMessage>}
    </>
  );
}
```

```ts
// middleware.ts — mount the auth gate, own the matcher
export { middleware } from "@carneirofc/magi-web/middleware";
export const config = { matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"] };
```

**Why the config literals stay in your file:** Next reads route-segment config
(`dynamic`/`runtime`) and the middleware `matcher` *statically* from the file at
the route path — it does not follow them through a package re-export. So the
handler/page/middleware **logic** comes from the library; the config **literals**
are declared in your thin file.

**No backend URL crosses the boundary.** The lib clients read `ADMIN_API_URL` /
`CHAT_API_URL` + `*_AUTH_TOKEN` (and `ADMIN_PASSWORD` / `SESSION_SECRET`) from
**your** `.env`. The library hardcodes nothing.

- `components/*` — `ChatConsole`, `AppShell`, `Sidebar`, `MemoryTabs`,
  `KnowledgeList`, `TeamView`, `LoginView`, `DashboardError`, `CodeBlock`, …
- `routes/*` — the BFF proxy handlers (`chat`, `admin/*`, `auth/*`, `identity/*`).
- `pages/*` — server page views (`dashboard`, `chat`, `team`, `memory`,
  `knowledge`, …); each also exports a copy-driven `…View` for reskinning.
- `middleware` — the session-cookie auth gate.
- `lib/*` — chat runtime (assistant-ui adapters, SSE, history/session/attachment/
  dictation), the typed admin API client (`api-types.ts`), shared utils.

## Extend

You reskin and compose; you don't fork. Prefer slice imports first:

| Want to change… | Where |
|---|---|
| Colors / fonts / accent | override `@carneirofc/ui` theme tokens in your `globals.css` |
| Brand wordmark / logo / nav | app-owned nav config + `MagiAppShell` / `AppShell` |
| Page copy (titles/descriptions) | the `…View` `copy` prop, or write a bespoke `app/` page |
| Which routes/pages exist | your thin `app/` tree — mount only what you want |
| Backend URL / auth / secrets | your `.env` (the lib clients read it) |
| A bespoke widget | a component in your app, composed alongside imported ones |
| The engine data contract | pin a `@carneirofc/magi-web` version built against that engine |

```tsx
// your app/(app)/chat/page.tsx — replace the screen composition without forking
import { ChatConsole } from "@carneirofc/magi-web/slices/chat/components";
import { chatCopy } from "@carneirofc/magi-web/slices/chat/screens";

export default function ChatPage() {
  return (
    <section>
      <h1>{chatCopy.title}</h1>
      <p>Custom header, custom layout, same stable chat building blocks.</p>
      <ChatConsole />
    </section>
  );
}
```

```tsx
// your app/(app)/layout.tsx — app-owned shell assembly
import { buildDefaultAppShellConfig, MagiAppShell } from "@carneirofc/magi-web/slices/shell";

const shell = buildDefaultAppShellConfig({ brand: "Ada", tagline: "Console" });

export default function Layout({ children }: { children: React.ReactNode }) {
  return <MagiAppShell nav={shell.nav} brand={shell.brand} tagline={shell.tagline}>{children}</MagiAppShell>;
}
```

Anything not on the table above is *mechanism* — it belongs in this library, not
your app. If you find yourself copy-pasting a component to tweak it, that's a
signal the component needs a prop, not a fork — open an issue/PR against this
package.

## Develop (in this repo)

The library is an npm **workspace**, so the reference app consumes it via a
symlink — edit a component and the app hot-reloads, no reinstall, no version bump
(the JS analog of the engine's `uv` editable path source).

```bash
cd web
npm install                                   # links the workspace
npm run dev                                    # runs the reference app on :3000
npm run typecheck -w @carneirofc/magi-web      # gate before release
```

Regenerate the typed API client from the engine's OpenAPI:

```bash
ADMIN_API_URL=http://127.0.0.1:8100 npm run gen:api
```

## Release

1. Bump `version` in this `package.json` (GitHub Packages rejects republishing an
   existing version).
2. Tag and push:
   ```bash
   git tag magi-web-v0.1.0 && git push origin magi-web-v0.1.0
   ```
3. [`.github/workflows/publish-magi-web.yml`](../../../.github/workflows/publish-magi-web.yml)
   installs the workspace, typechecks, and runs `npm publish -w @carneirofc/magi-web`
   with the job's `GITHUB_TOKEN`. No external secrets.

Full architecture + migration context: [`docs/web-extensibility-plan.md`](../../../docs/web-extensibility-plan.md) and [`docs/frontend-split.md`](../../../docs/frontend-split.md).
