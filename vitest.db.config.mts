import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

/**
 * Database tests: apply real migrations to a disposable local PostgreSQL
 * (scripts/db-test.sh up) and exercise Row Level Security as anon,
 * authenticated users, and the service role.
 */
export default defineConfig({
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
      "server-only": fileURLToPath(new URL("./tests/support/empty.ts", import.meta.url)),
    },
  },
  test: {
    include: ["tests/db/**/*.test.ts"],
    environment: "node",
    globalSetup: ["tests/db/global-setup.ts"],
    fileParallelism: false,
    testTimeout: 30_000,
    hookTimeout: 60_000,
    env: { APP_ENV: "test", LOG_LEVEL: "error" },
  },
});
