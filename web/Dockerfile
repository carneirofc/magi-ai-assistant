# syntax=docker/dockerfile:1

# Next.js admin UI + BFF, built as a standalone Node server (next.config.mjs:
# output: "standalone"). Multi-stage so the runtime image carries only the
# server bundle + static assets, not the toolchain or node_modules source.

FROM node:22-bookworm-slim AS deps
WORKDIR /app
# .npmrc scopes @carneirofc/* to GitHub Packages, which needs a read:packages
# token. Pass it as a build secret so it never lands in an image layer:
#   docker build --secret id=node_auth_token,env=NODE_AUTH_TOKEN ...
COPY package.json package-lock.json* .npmrc ./
# npm workspaces: the member manifest must be present before install so the
# lockfile's workspace link resolves (@carneirofc/magi-web is local, not fetched).
COPY packages/magi-web/package.json ./packages/magi-web/package.json
# Use the lockfile when present; fall back to a plain install otherwise.
RUN --mount=type=secret,id=node_auth_token \
    NODE_AUTH_TOKEN="$(cat /run/secrets/node_auth_token 2>/dev/null || true)" \
    sh -c 'npm ci 2>/dev/null || npm install'

FROM node:22-bookworm-slim AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:22-bookworm-slim AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000
# Standalone output: a self-contained server + the static assets it serves.
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
EXPOSE 3000
CMD ["node", "server.js"]
