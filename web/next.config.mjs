/** @type {import('next').NextConfig} */
const nextConfig = {
  // Standalone output so the Docker image is a self-contained Node server (the
  // BFF/route-handlers need a server — no static export). See web/Dockerfile.
  output: "standalone",
};

export default nextConfig;
