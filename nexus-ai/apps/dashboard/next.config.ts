import path from "node:path";
import type { NextConfig } from "next";

// Static export: served by Nginx (production) or the API's static mount (dev/test).
// No Node.js server runs in production.
const config: NextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  poweredByHeader: false,
  reactStrictMode: true,
  // This app lives inside a repo with another Next.js project: pin the workspace root.
  turbopack: { root: path.resolve(__dirname) },
  outputFileTracingRoot: path.resolve(__dirname),
};

export default config;
