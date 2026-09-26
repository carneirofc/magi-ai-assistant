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
- **Runtime validation with zod.** Admin-api schemas are generated into
  `src/lib/api-schemas.ts` (`schemas.<Name>`) alongside `api-types.ts`. Shapes
  outside that OpenAPI (chat-api responses, BFF route bodies, transcripts,
  localStorage) live in `src/lib/wire-schemas.ts`; runtime-config shapes in
  `src/lib/runtime-config-schema.ts` (client-safe, unlike `runtime-config.ts`).
  A schema mirroring an exported interface is typed `z.ZodType<Interface>`.
  Parse responses with `fetchJson(res, Schema)` (`src/lib/utils.ts`) or
  `Schema.parse(await res.json())`; route handlers read bodies with
  `readJsonBody` + `invalidBody()` (`src/lib/route-body.ts`) so a bad body is a
  400, not a 500. `zod` is a runtime dependency.
- The package is **presentational + runtime only** — the reference app owns route
  mounting, shell assembly, and env/secret wiring.

# Verification

From `web/`: `npm run lint` and `npm run typecheck -w @carneirofc/magi-web`. `sideEffects: false` — keep
modules side-effect-free so tree-shaking holds.
