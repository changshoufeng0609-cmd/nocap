import type { NextConfig } from "next";
import { PHASE_DEVELOPMENT_SERVER } from "next/constants";

// Mock mode (NEXT_PUBLIC_RECEIPTS_MOCK=1) is served by app/mock-api; otherwise the browser calls
// /api/*, which `next dev` proxies to the Python API (receipts/api.py) on localhost:8000.
const MOCK = process.env.NEXT_PUBLIC_RECEIPTS_MOCK === "1";
const API_ORIGIN = process.env.RECEIPTS_API_ORIGIN ?? "http://localhost:8000";

export default function config(phase: string): NextConfig {
  const dev = phase === PHASE_DEVELOPMENT_SERVER;
  return {
    // The dev proxy would otherwise gzip /api/check and hold every SSE event until the stream ends
    // (unless the API sends Cache-Control: no-transform). Vercel compresses at its own edge.
    compress: !dev,
    // No route badge over the page when a demo is recorded from `next dev` (errors still show).
    devIndicators: false,
    cacheComponents: true,
    partialPrefetching: true,
    turbopack: {
      rules: {
        "*.css": {
          loaders: ["@tailwindcss/turbopack"],
          as: "*.css",
        },
      },
    },
    async rewrites() {
      if (MOCK || !dev) return [];
      return [{ source: "/api/:path*", destination: `${API_ORIGIN}/api/:path*` }];
    },
  };
}
