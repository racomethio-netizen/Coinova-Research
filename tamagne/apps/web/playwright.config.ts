import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end: real API (built dist) + real Next.js server against the disposable
 * test database. SMS goes to the API's in-memory test outbox (APP_ENV=test only).
 * Prerequisites: `npm run db:test:up` and `npm run build` at the repo root.
 */
const key = (n: number) => Buffer.alloc(32, n).toString("base64");
const WEB = "http://localhost:3100";
const API = "http://127.0.0.1:4100";

export default defineConfig({
  testDir: "e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  use: { baseURL: WEB, trace: "off" },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile", use: { ...devices["Pixel 7"] } },
  ],
  webServer: [
    {
      command: "node ../api/dist/main.js",
      url: `${API}/api/v1/health`,
      reuseExistingServer: false,
      env: {
        APP_ENV: "test",
        PORT: "4100",
        DATABASE_URL: process.env.TEST_DATABASE_URL ?? "postgresql://postgres@127.0.0.1:54339/tamagne_test",
        FIELD_ENCRYPTION_KEYS: `1:${key(1)}`,
        FIELD_ENCRYPTION_ACTIVE_KEY_ID: "1",
        HMAC_PEPPER: key(2),
        CORS_ORIGINS: WEB,
        PUBLIC_WEB_URL: WEB,
        SMS_PROVIDER: "test",
        ALLOW_TEST_PHONE_NUMBERS: "true",
        TRUST_PROXY_HOPS: "1",
        STORAGE_DRIVER: "local",
        STORAGE_LOCAL_DIR: "../../.tmp/e2e-storage",
        SCANNER: "eicar-only",
      },
    },
    {
      command: "npx next start --port 3100",
      url: `${WEB}/en`,
      reuseExistingServer: false,
      env: { API_INTERNAL_URL: API, NEXT_TELEMETRY_DISABLED: "1" },
    },
  ],
});
