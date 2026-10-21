import { expect, test } from "@playwright/test";

test.describe("public site", () => {
  test("home page shows market overview, mock-data warning, and disclaimer", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { level: 1 })).toContainText("Crypto research you can audit");
    await expect(page.getByText(/MOCK DATA for development/)).toBeVisible();
    await expect(page.getByRole("table", { name: /market data/i })).toBeVisible();
    await expect(page.getByText(/not financial, investment, legal, or tax advice/)).toBeVisible();
  });

  test("markets: search, sort, and navigate to a coin", async ({ page }) => {
    await page.goto("/markets");
    await expect(page.getByText("Mock data — not real prices").first()).toBeVisible();
    await page.getByLabel("Search").fill("aurora");
    await page.getByRole("button", { name: "Apply" }).click();
    await expect(page).toHaveURL(/q=aurora/);
    const rows = page.locator("tbody tr");
    await expect(rows).toHaveCount(1);
    await rows.first().getByRole("link").click();
    await expect(page).toHaveURL(/\/coins\/mock-aurora$/);
    await expect(page.getByRole("heading", { name: "Price history" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Supply & tokenomics" })).toBeVisible();
  });

  test("markets sorting by price descending orders rows", async ({ page }) => {
    await page.goto("/markets?sort=price&dir=desc");
    const prices = await page.locator("tbody tr td:nth-child(3)").allTextContents();
    const nums = prices.map((p) => Number(p.replace(/[^0-9.]/g, "")));
    expect(nums.length).toBeGreaterThan(5);
    for (let i = 1; i < nums.length; i++) expect(nums[i - 1]!).toBeGreaterThanOrEqual(nums[i]!);
  });

  test("sample coin shows a score breakdown; non-sample coin is locked for free users", async ({ page }) => {
    await page.goto("/coins/mock-aurora");
    await expect(page.getByText(/Methodology v1\.0\.0/)).toBeVisible();
    await page.goto("/coins/mock-zephyr");
    await expect(page.getByText("Research score available with Premium")).toBeVisible();
  });

  test("chart period switch loads history through the API", async ({ page }) => {
    await page.goto("/coins/mock-aurora");
    const res = page.waitForResponse((r) => r.url().includes("/api/coins/mock-aurora/history?period=7d"));
    await page.getByRole("button", { name: "7D" }).click();
    expect((await res).status()).toBe(200);
    await expect(page.getByRole("button", { name: "7D" })).toHaveAttribute("aria-pressed", "true");
  });

  test("pages render without client-side errors (including the chart)", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    for (const path of ["/", "/markets", "/coins/mock-aurora", "/pricing", "/login"]) {
      await page.goto(path);
      await page.waitForLoadState("networkidle");
    }
    expect(errors).toEqual([]);
    await page.goto("/coins/mock-aurora");
    await expect(page.locator('[aria-label="Historical price chart"] canvas').first()).toBeVisible();
  });

  test("unknown coins return the not-found page", async ({ page }) => {
    const res = await page.goto("/coins/not-a-real-coin");
    expect(res?.status()).toBe(404);
    await expect(page.getByText("Page not found")).toBeVisible();
  });

  test("pricing shows plans, comparison, and cancellation terms", async ({ page }) => {
    await page.goto("/pricing");
    await expect(page.getByText("Premium — Monthly")).toBeVisible();
    await expect(page.getByText("$19")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Compare features" })).toBeVisible();
    await page.getByText("How do I cancel?").click();
    await expect(page.getByText(/Cancellation takes effect at the end of the current paid period/)).toBeVisible();
  });

  test("methodology page documents weights and limitations", async ({ page }) => {
    await page.goto("/about");
    await expect(page.getByText("25% weight")).toBeVisible();
    await expect(page.getByText(/Missing inputs are never treated as poor quality/)).toBeVisible();
  });

  test("research page handles an unavailable database gracefully", async ({ page }) => {
    await page.goto("/research");
    await expect(page.getByText("Research is not available right now")).toBeVisible();
  });

  test("no horizontal overflow on key pages", async ({ page }) => {
    for (const path of ["/", "/markets", "/coins/mock-aurora", "/pricing"]) {
      await page.goto(path);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow, `overflow on ${path}`).toBeLessThanOrEqual(1);
    }
  });

  test("theme toggle switches to light mode", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "Toggle light and dark theme" }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  });
});

test.describe("auth and protection", () => {
  test("dashboard and admin redirect signed-out users to login", async ({ page }) => {
    await page.goto("/dashboard/portfolio");
    await expect(page).toHaveURL(/\/login\?next=%2Fdashboard%2Fportfolio/);
    await page.goto("/admin");
    await expect(page).toHaveURL(/\/login\?next=%2Fadmin/);
  });

  test("login form reports validation errors accessibly", async ({ page }) => {
    await page.goto("/login");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByText("Enter a valid email address.")).toBeVisible();
    await expect(page.getByLabel("Email")).toHaveAttribute("aria-invalid", "true");
  });

  test("register form validates password strength and consent", async ({ page }) => {
    await page.goto("/register");
    await page.getByLabel("Email").fill("someone@example.com");
    await page.getByLabel("Password", { exact: true }).fill("short");
    await page.getByLabel("Confirm password").fill("different");
    await page.getByRole("button", { name: "Create account" }).click();
    await expect(page.getByText("Use at least 10 characters.")).toBeVisible();
    await expect(page.getByText("You must accept the terms and risk disclosure.")).toBeVisible();
  });

  test("login explains when authentication is not configured", async ({ page }) => {
    await page.goto("/login");
    await expect(page.getByText(/Authentication is not configured on this server/)).toBeVisible();
  });
});

test.describe("API", () => {
  test("health endpoint reports status without secrets", async ({ request }) => {
    const res = await request.get("/api/health");
    expect(res.status()).toBe(200);
    const body = await res.json();
    expect(body.status).toBe("ok");
    expect(body.checks.marketData).toBe("mock");
    expect(JSON.stringify(body)).not.toMatch(/key|secret|token/i);
  });

  test("markets API validates and paginates", async ({ request }) => {
    const res = await request.get("/api/markets?perPage=10&page=2&sort=marketCap&dir=desc");
    const body = await res.json();
    expect(body.ok).toBe(true);
    expect(body.data.items).toHaveLength(10);
    expect(body.data.source).toBe("mock");
  });

  test("history API rejects invalid periods", async ({ request }) => {
    const res = await request.get("/api/coins/mock-aurora/history?period=10y");
    expect(res.status()).toBe(422);
  });

  test("protected APIs refuse unauthenticated and cross-origin requests", async ({ request }) => {
    const cross = await request.post("/api/portfolio/transactions", { headers: { origin: "https://evil.example", "content-type": "application/json" }, data: {} });
    expect(cross.status()).toBe(403);
    const get = await request.get("/api/portfolio");
    expect([401, 503]).toContain(get.status());
    expect((await get.json()).ok).toBe(false);
  });

  test("billing webhook refuses requests when billing is not configured", async ({ request }) => {
    const res = await request.post("/api/billing/webhook", { data: { type: "subscription.activated" } });
    expect(res.status()).toBe(503);
  });

  test("cron endpoint requires the secret", async ({ request }) => {
    const res = await request.post("/api/cron/refresh-markets");
    expect([401, 503]).toContain(res.status());
  });

  test("security headers are set", async ({ request }) => {
    const res = await request.get("/");
    const h = res.headers();
    expect(h["content-security-policy"]).toContain("frame-ancestors 'none'");
    expect(h["x-frame-options"]).toBe("DENY");
    expect(h["x-content-type-options"]).toBe("nosniff");
    expect(h["x-powered-by"]).toBeUndefined();
  });
});
