import { createHmac } from "node:crypto";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

// Mirrors tests/integration/jwt.ts (config files cannot import TS test helpers reliably).
const SECRET = "local-integration-test-secret-not-for-production-use";
const b64 = (v: unknown) => Buffer.from(JSON.stringify(v)).toString("base64url");
function jwt(claims: Record<string, unknown>) {
  const h = b64({ alg: "HS256", typ: "JWT" });
  const p = b64({ iat: Math.floor(Date.now() / 1000), exp: Math.floor(Date.now() / 1000) + 3600, ...claims });
  return `${h}.${p}.${createHmac("sha256", SECRET).update(`${h}.${p}`).digest("base64url")}`;
}

/**
 * Integration tests: server modules (billing webhooks, jobs, Telegram
 * linking, repositories, AI quota) against real Postgres + PostgREST.
 */
export default defineConfig({
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
      "server-only": fileURLToPath(new URL("./tests/support/empty.ts", import.meta.url)),
    },
  },
  test: {
    include: ["tests/integration/**/*.test.ts"],
    environment: "node",
    globalSetup: ["tests/integration/global-setup.ts"],
    fileParallelism: false,
    testTimeout: 60_000,
    hookTimeout: 120_000,
    env: {
      APP_ENV: "test",
      LOG_LEVEL: "error",
      NEXT_PUBLIC_SITE_URL: "http://localhost:3000",
      SUPABASE_URL: "http://127.0.0.1:54331",
      SUPABASE_ANON_KEY: jwt({ role: "anon" }),
      SUPABASE_SERVICE_ROLE_KEY: jwt({ role: "service_role" }),
      MARKET_DATA_PROVIDER: "mock",
      BILLING_PROVIDER: "dev_simulator",
      DEV_BILLING_WEBHOOK_SECRET: "integration-test-billing-secret-0123456789",
      TELEGRAM_BOT_TOKEN: "000000:test-token-not-real",
      TELEGRAM_BOT_USERNAME: "cryptoscope_test_bot",
      TELEGRAM_WEBHOOK_SECRET: "integration-telegram-secret-0123456789",
      EMAIL_PROVIDER: "console",
      HISTORY_REFRESH_DELAY_MS: "0",
    },
  },
});
