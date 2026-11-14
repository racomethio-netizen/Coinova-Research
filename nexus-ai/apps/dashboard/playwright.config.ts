import { defineConfig, devices } from "@playwright/test";

// Expects the API (serving this static build) at NEXUS_E2E_URL; see scripts/e2e.sh.
export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  retries: 0,
  use: {
    baseURL: process.env.NEXUS_E2E_URL ?? "http://127.0.0.1:8100",
    trace: "off",
    launchOptions: process.env.PW_CHROMIUM ? { executablePath: process.env.PW_CHROMIUM } : {},
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile", use: { ...devices["Desktop Chrome"], viewport: { width: 360, height: 780 } } },
  ],
});
