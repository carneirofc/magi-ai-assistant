/** @type {import('next').NextConfig} */
const nextConfig = {
  // Standalone output so the Docker image is a self-contained Node server (the
  // BFF/route-handlers need a server — no static export). See web/Dockerfile.
  output: "standalone",
  // The UI + chat runtime live in the @carneirofc/magi-web workspace package as
  // TypeScript source; Next compiles it here (and resolves its RSC "use client"
  // boundary). Downstream persona overlays need the same line. See
  // packages/magi-web/README.md and docs/frontend-split.md.
  transpilePackages: ["@carneirofc/magi-web"],
};

export default nextConfig;
