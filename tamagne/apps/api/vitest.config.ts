import swc from "unplugin-swc";
import { defineConfig } from "vitest/config";

export default defineConfig({
  // SWC emits decorator metadata, which Nest's dependency injection needs.
  plugins: [swc.vite({ module: { type: "es6" } })],
  resolve: { conditions: ["tamagne-source"] },
  ssr: { resolve: { conditions: ["tamagne-source"], externalConditions: ["tamagne-source"] } },
  test: { include: ["test/**/*.test.ts"], testTimeout: 20_000 },
});
