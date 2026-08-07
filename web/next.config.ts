import type { NextConfig } from "next";

// In development, proxy API and WebSocket calls to the backend so the browser
// talks to the same origin (no CORS). Override the target with API_PROXY_TARGET.
const apiTarget = process.env.API_PROXY_TARGET ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${apiTarget}/api/:path*` },
      { source: "/ws/:path*", destination: `${apiTarget}/ws/:path*` },
    ];
  },
};

export default nextConfig;
