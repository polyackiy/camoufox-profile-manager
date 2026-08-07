import type { NextConfig } from "next";

// Two build modes:
//  - default (server): dev and `next start`. Proxies /api and /ws to the backend
//    so the browser talks to the same origin (no CORS). Target: API_PROXY_TARGET.
//  - static export (NEXT_EXPORT=1): emits a static `out/` that FastAPI serves on
//    the same origin as the API, so no rewrites are needed. Used by the
//    single-process `camoufox-pm` launcher.
const isExport = process.env.NEXT_EXPORT === "1";
const apiTarget = process.env.API_PROXY_TARGET ?? "http://localhost:8000";

const nextConfig: NextConfig = isExport
  ? { output: "export", trailingSlash: true, images: { unoptimized: true } }
  : {
      async rewrites() {
        return [
          { source: "/api/:path*", destination: `${apiTarget}/api/:path*` },
          { source: "/ws/:path*", destination: `${apiTarget}/ws/:path*` },
        ];
      },
    };

export default nextConfig;
