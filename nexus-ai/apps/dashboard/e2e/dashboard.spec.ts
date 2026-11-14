import { expect, test, type Page } from "@playwright/test";

const EMAIL = process.env.NEXUS_E2E_EMAIL ?? "";
const PASSWORD = process.env.NEXUS_E2E_PASSWORD ?? "";

const PAGES: [string, string][] = [
  ["/", "Overview"], ["/portfolio/", "Portfolio"], ["/exchanges/", "Exchanges"],
  ["/markets/", "Live markets"], ["/signals/", "AI signals"], ["/positions/", "Open positions"],
  ["/orders/", "Order history"], ["/trades/", "Trade history"], ["/performance/", "Performance analytics"],
  ["/strategies/", "Strategy configuration"], ["/models/", "AI model monitoring"],
  ["/risk/", "Risk management"], ["/system/", "System monitoring"], ["/security/", "Security settings"],
];

async function login(page: Page) {
  await page.goto("/login/");
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
}

test("signed-out visitors are sent to login", async ({ page }) => {
  await page.goto("/portfolio/");
  await expect(page).toHaveURL(/\/login\/$/);
});

test("wrong password shows a generic error", async ({ page }) => {
  await page.goto("/login/");
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password").fill("definitely-wrong-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.locator("form").getByRole("alert")).toHaveText("invalid email or password");
});

test("every page renders real data without errors", async ({ page }) => {
  const problems: string[] = [];
  page.on("pageerror", (e) => problems.push(e.message));
  page.on("console", (m) => { if (m.type() === "error") problems.push(m.text()); });
  await login(page);
  for (const [path, heading] of PAGES) {
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
    await expect(page.getByText("Loading…")).toHaveCount(0, { timeout: 10_000 });
    await expect(page.getByText("Could not load")).toHaveCount(0);
    await expect(page.getByText(/PAPER TRADING: simulated fills/)).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow, `horizontal overflow on ${path}`).toBeLessThanOrEqual(1);
  }
  expect(problems).toEqual([]);
});

test("validation results are shown honestly", async ({ page }) => {
  await login(page);
  await page.goto("/strategies/");
  await expect(page.getByText("failed").first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Enable" }).first()).toBeDisabled();
  await page.goto("/models/");
  await expect(page.getByText("rejected").first()).toBeVisible();
});

test("market chart renders real candles", async ({ page }) => {
  await login(page);
  await page.goto("/markets/");
  await expect(page.getByRole("img", { name: /BTC\/USDT 1h candles on binance/ })).toBeVisible();
  await expect(page.locator("canvas").first()).toBeVisible();
});

test("kill switch can be engaged from the risk page", async ({ page }, info) => {
  test.skip(info.project.name !== "desktop", "mutates shared state; run once");
  await login(page);
  await page.goto("/risk/");
  await page.getByLabel("Reason (required)").fill("e2e emergency stop test");
  await page.getByRole("button", { name: "Engage kill switch" }).click();
  await expect(page.getByText("ENGAGED", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Clear kill switch" })).toBeVisible();
});
