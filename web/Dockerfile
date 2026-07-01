# syntax=docker/dockerfile:1

# Next.js admin UI + BFF, built as a standalone Node server (next.config.mjs:
# output: "standalone"). Multi-stage so the runtime image carries only the
# server bundle + static assets, not the toolchain or node_modules source.

FROM node:22-bookworm-slim AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
# Use the lockfile when present; fall back to a plain install otherwise.
RUN npm ci 2>/dev/null || npm install

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
