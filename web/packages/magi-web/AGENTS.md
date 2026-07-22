# Purpose

`@carneirofc/magi-web`: the presentational React components and SSE chat runtime for
building magi admin/chat UIs. Published to GitHub Packages; consumed from **source**
(no build step) via Next.js `transpilePackages`.

# Local Contracts

- **The public API is the `exports` map in `package.json`.** Everything a consumer
  imports (`components/*`, `lib/*`, `slices/*`, `routes/*`, `pages/*`, `middleware`)
  is an explicit export. Adding a consumer-facing module means adding its export
  entry; keep the map and the actual files in sync. This is a published, versioned
  package — bump the version (SemVer) on any consumer-visible change.
- **Slice architecture** (`src/slices/<feature>/`): each feature exposes stable seams
  in the order `types → hooks → components → screens → routes`. Consumers compose
  from building blocks first; `screens`/`pages` are convenience, not the primary API.
  Keep these seams stable — they are the overlay contract.
- **Server/client split.** Modules under `lib/` that hold a bearer or hit an upstream
  (`admin-api.ts`, `chat-api.ts`) are server-only; never import them into client
  components. `middleware.ts` and `routes/*` run on the server.
- The package is **presentational + runtime only** — the reference app owns route
  mounting, shell assembly, and env/secret wiring.

# Verification

From `web/`: `npm run typecheck -w @carneirofc/magi-web`. `sideEffects: false` — keep
modules side-effect-free so tree-shaking holds.
