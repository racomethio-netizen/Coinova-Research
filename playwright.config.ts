import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests run against a production build (`next build && next start`)
 * with the MOCK market provider and no Supabase, so they are deterministic
 * and need no external accounts. Flows that need a real Supabase Auth
 * instance (sign-up, sign-in) are covered by docs/TESTING.md#manual-checks.
 */
const PORT = 3100;

export default defineConfig({
  testDir: "tests/e2e",
  timeout: 30_000,
  fullyParallel: true,
  retries: 0,
  reporter: [["list"]],
  use: { baseURL: `http://127.0.0.1:${PORT}`, trace: "retain-on-failure" },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile", use: { ...devices["Pixel 7"] } },
  ],
  webServer: {
    command: `npx next start -p ${PORT}`,
    url: `http://127.0.0.1:${PORT}/api/health`,
    reuseExistingServer: false,
    timeout: 60_000,
    env: { APP_ENV: "test", MARKET_DATA_PROVIDER: "mock", NEXT_PUBLIC_SITE_URL: `http://127.0.0.1:${PORT}`, LOG_LEVEL: "error" },
  },
});
