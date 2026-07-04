# @carneirofc/magi-web

The **MAGI frontend library**: presentational React components and the SSE chat
runtime the admin/chat UI is built from. The frontend twin of the `magi` Python
engine — *mechanism* (components, chat runtime, typed API client) lives here and
is reused; *policy* (branding, theme, which pages exist, backend URLs, auth)
stays in the consuming app.

Published to **GitHub Packages** under `@carneirofc` (the scope is bound to the
repo owner; "magi" lives in the package name). Consumed by the in-repo
`magi-admin-web` reference app and by private persona overlays (e.g. `alyssa`).

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

Import by subpath (mirrors the reference app's old `@/components` / `@/lib`):

```tsx
import { ChatConsole } from "@carneirofc/magi-web/components/ChatConsole";
import { AppShell } from "@carneirofc/magi-web/components/AppShell";
import { createSessionHistoryAdapter } from "@carneirofc/magi-web/lib/chat-history";
```

`ChatConsole` takes no props — it streams against **fixed same-origin BFF paths**
(`/api/chat`, `/api/chat/blobs/*`). That's the integration contract: your app
implements those routes, and that's where the engine URL + auth token live. The
library never sees a backend URL. Copy the reference app's `src/app/api/chat/`
handlers as your starting point.

- `components/*` — `ChatConsole`, `AppShell`, `Sidebar`, `MemoryTabs`,
  `KnowledgeList`, `TeamView`, `CodeBlock`, `MermaidDiagram`, …
- `lib/*` — chat runtime (assistant-ui adapters, SSE, history/session/attachment/
  dictation), the typed admin API client (`api-types.ts`), shared utils.

## Extend

You reskin and compose; you don't fork. The seams:

| Want to change… | Where |
|---|---|
| Colors / fonts / accent | override `@carneirofc/ui` theme tokens in your `globals.css` |
| Which pages exist | your own `app/` routes — import only the components you want |
| Backend URL / auth / secrets | your own `app/api/*` BFF route handlers |
| A bespoke widget | a component in your app, composed alongside imported ones |
| The engine data contract | pin a `@carneirofc/magi-web` version built against that engine |

```tsx
// your app/dashboard/page.tsx — compose library + your own components
import { AppShell } from "@carneirofc/magi-web/components/AppShell";
import { StatCard } from "@carneirofc/magi-web/components/StatCard";
import { MyCustomPanel } from "@/components/MyCustomPanel";

export default function Dashboard() {
  return (
    <AppShell>
      <StatCard label="Users" value={42} />
      <MyCustomPanel />
    </AppShell>
  );
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

Full architecture + the persona-overlay walkthrough: [`docs/frontend-split.md`](../../../docs/frontend-split.md).
