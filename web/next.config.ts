import type { NextConfig } from "next";

// Two build modes:
//  - default (server): dev and `next start`. Proxies /api to the backend
//    so the browser talks to the same origin (no CORS). Target: API_PROXY_TARGET.
//  - static export (NEXT_EXPORT=1): emits a static `out/` that FastAPI serves on
//    the same origin as the API, so no rewrites are needed. Used by the
//    single-process `camoufox-pm` launcher.
const isExport = process.env.NEXT_EXPORT === "1";
const apiTarget = process.env.API_PROXY_TARGET ?? "http://localhost:8000";

// Next 16 writes AGENTS.md and CLAUDE.md into `web/` on the first `next dev`.
// They are framework boilerplate that says nothing about this project, and they
// would sit untracked in every working tree, so decline them in both modes.
const shared = { agentRules: false } satisfies NextConfig;

const nextConfig: NextConfig = isExport
  ? { ...shared, output: "export", trailingSlash: true, images: { unoptimized: true } }
  : {
      ...shared,
      async rewrites() {
        return [
          { source: "/api/:path*", destination: `${apiTarget}/api/:path*` },
        ];
      },
    };

export default nextConfig;
