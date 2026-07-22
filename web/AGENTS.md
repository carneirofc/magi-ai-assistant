# Purpose

The MAGI web frontend. An npm workspace with two products:
- **`magi-admin-web`** (this dir, `web/src/`) — the reference Next.js App Router app
  and **BFF** (backend-for-frontend) that operators run.
- **`@carneirofc/magi-web`** (`packages/magi-web/`) — the presentational React +
  SSE-runtime library the app composes. Consumed from source via
  `transpilePackages`.

# Local Contracts

- **BFF security model — the browser only ever talks to this Next.js server.** It
  holds upstream bearer tokens **server-side** and gates the operator with a
  password → httpOnly session cookie (`middleware.ts`, `lib/session.ts`). Two
  upstreams: the Python `admin-api` (`ADMIN_API_URL` + `ADMIN_AUTH_TOKEN`, memory &
  knowledge) and the `chat-api` (`CHAT_API_URL` + `API_AUTH_TOKEN`, the running
  team, SSE). Neither token may ever reach the browser; never expose the Python APIs
  publicly. See [ADR 0002](../docs/adr/0002-admin-interface-for-memory-and-knowledge.md).
- **All secrets are server-side only** — the config vars in `README.md` are never
  shipped to the browser.
- **`src/lib/api-types.ts` is generated** — `npm run gen:api` regenerates it from the
  live admin-api OpenAPI. Never hand-edit it; regenerate when a channel endpoint
  changes.
- **App owns routes and shell; library owns pieces.** The golden path (e.g. the
  `/chat` route) is an app-local page that reuses stable library slice exports — not
  a bare re-export. Prefer slice building blocks (`types + hooks + components`) over
  convenience `pages/*`.
- **Design system** is `@carneirofc/ui` (Tailwind v4 + Radix), pinned via peer/dep
  range. Theme is `data-theme` on `<html>`. In local co-dev the workspace links
  `@carneirofc/ui` from a sibling checkout; CI/publish repoint it to the GitHub
  Packages registry — do not commit that rewrite or a lockfile assuming the local
  path.

# Verification

From `web/`: `npm run typecheck` (app) and `npm run typecheck -w @carneirofc/magi-web`
(library). There is no separate test suite; typecheck is the gate.

# Child Index

- `packages/magi-web/` — the `@carneirofc/magi-web` library (slice architecture,
  components, server clients, generated types).
