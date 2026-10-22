import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const routes = [
  "/",
  "/services",
  "/services/custom-software-development",
  "/work",
  "/work/ledgerline",
  "/products",
  "/about",
  "/insights",
  "/insights/scoping-software-projects",
  "/contact",
  "/privacy",
  "/terms",
];

test.describe("pages", () => {
  for (const route of routes) {
    test(`${route} renders with one h1, metadata and no a11y violations`, async ({ page }) => {
      const res = await page.goto(route);
      expect(res?.status()).toBe(200);
      await expect(page.locator("h1")).toHaveCount(1);
      await expect(page).toHaveTitle(/Racom Technology/);
      expect(await page.locator('meta[name="description"]').getAttribute("content")).toBeTruthy();
      expect(await page.locator('meta[property="og:title"]').getAttribute("content")).toBeTruthy();
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow).toBe(0);
      const results = await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
        .analyze();
      expect(results.violations.map((v) => `${v.id}: ${v.nodes.length}`)).toEqual([]);
    });
  }

  test("unknown routes return a helpful 404", async ({ page }) => {
    const res = await page.goto("/does-not-exist");
    expect(res?.status()).toBe(404);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("This page isn’t on the map.");
    await expect(page.getByRole("link", { name: "Back to home" })).toBeVisible();
  });

  test("sitemap, robots, icons and OG image are served", async ({ request }) => {
    const sitemap = await request.get("/sitemap.xml");
    expect(sitemap.ok()).toBe(true);
    const xml = await sitemap.text();
    for (const path of ["/services/ai-automation", "/work/bridgeport", "/insights/practical-ai-automation"]) {
      expect(xml).toContain(path);
    }
    expect((await request.get("/robots.txt")).ok()).toBe(true);
    expect((await request.get("/icon.svg")).ok()).toBe(true);
    const og = await request.get("/opengraph-image");
    expect(og.headers()["content-type"]).toContain("image/png");
  });

  test("security headers are set", async ({ request }) => {
    const res = await request.get("/");
    const h = res.headers();
    expect(h["content-security-policy"]).toContain("default-src 'self'");
    expect(h["x-frame-options"]).toBe("DENY");
    expect(h["x-content-type-options"]).toBe("nosniff");
    expect(h["x-powered-by"]).toBeUndefined();
  });
});

test.describe("navigation", () => {
  test("homepage CTAs and capability links work", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("main").getByRole("link", { name: "Start a Project" }).click();
    await expect(page).toHaveURL(/\/contact$/);
    await page.goto("/");
    await page.getByRole("link", { name: "Explore Our Work" }).click();
    await expect(page).toHaveURL(/\/work$/);
    await page.goto("/");
    await page.getByRole("link", { name: "AI & Business Automation" }).click();
    await expect(page).toHaveURL(/\/services\/ai-automation$/);
  });

  test("every internal link resolves", async ({ page, request }) => {
    const seen = new Set<string>();
    for (const route of routes) {
      await page.goto(route);
      const hrefs = await page
        .locator("a[href^='/']")
        .evaluateAll((as) => as.map((a) => a.getAttribute("href")!));
      for (const href of hrefs) seen.add(href.split("#")[0]!);
    }
    for (const href of seen) {
      const res = await request.get(href);
      expect(res.status(), href).toBe(200);
    }
  });

  test("concept work is clearly labelled", async ({ page }) => {
    await page.goto("/work/atlas-dispatch");
    await expect(page.getByText("Concept project").first()).toBeVisible();
    await expect(
      page.getByText("This is a concept project, so there are no client results to report."),
    ).toBeVisible();
  });
});

test.describe("mobile menu", () => {
  test("opens, traps focus, closes with Escape and navigates", async ({ page, isMobile }) => {
    test.skip(!isMobile, "mobile only");
    await page.goto("/");
    const toggle = page.getByRole("button", { name: "Open menu" });
    await toggle.click();
    const dialog = page.getByRole("dialog", { name: "Site navigation" });
    await expect(dialog).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Mobile" }).getByRole("link").first()).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await expect(toggle).toBeFocused();
    await toggle.click();
    await dialog.getByRole("link", { name: "Products" }).click();
    await expect(page).toHaveURL(/\/products$/);
    await expect(dialog).toBeHidden();
  });
});

test.describe("work filter", () => {
  test("filters projects by category", async ({ page }) => {
    await page.goto("/work");
    await expect(
      page.getByRole("heading", { level: 2, name: /Ledgerline|Atlas Dispatch|Signal Desk|Bridgeport/ }),
    ).toHaveCount(4);
    await page.getByRole("button", { name: "AI Automation" }).click();
    await expect(page.getByRole("button", { name: "AI Automation" })).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByText("Showing 1 project in AI Automation")).toBeVisible();
    await expect(page.getByRole("heading", { level: 2, name: "Signal Desk" })).toBeVisible();
    await expect(page.getByRole("heading", { level: 2, name: "Ledgerline" })).toHaveCount(0);
    await page.getByRole("button", { name: "All" }).click();
    await expect(page.getByText("Showing 4 projects")).toBeVisible();
  });
});

test.describe("contact form", () => {
  async function fill(page: import("@playwright/test").Page) {
    await page.getByLabel("Name").fill("Abebe Kebede");
    await page.getByLabel("Work email").fill("abebe@example.com");
    await page.getByLabel("Service needed").selectOption("web-development");
    await page
      .getByLabel("Project description")
      .fill("We need a new website that loads quickly on mobile connections.");
    await page.getByLabel(/I agree/).check();
    // The server rejects forms submitted faster than a person could type.
    await page.waitForTimeout(3_200);
  }

  test("validates fields accessibly", async ({ page }) => {
    await page.goto("/contact");
    await page.getByRole("button", { name: "Send inquiry" }).click();
    await expect(page.getByLabel("Name")).toHaveAttribute("aria-invalid", "true");
    await expect(page.getByText("Please enter your work email.")).toBeVisible();
    await expect(page.getByLabel("Name")).toBeFocused();
  });

  test("reports failure honestly when email delivery is not configured", async ({ page }) => {
    await page.goto("/contact");
    await fill(page);
    const response = page.waitForResponse("**/api/contact");
    await page.getByRole("button", { name: "Send inquiry" }).click();
    expect((await response).status()).toBe(503);
    // Scope to the form: Next.js also renders an (empty) role="alert" route announcer.
    const alert = page.getByRole("alert").filter({ hasText: "Your inquiry was not sent." });
    await expect(alert).toBeVisible();
    await expect(page.getByText(/Thank you/)).toHaveCount(0);
  });

  test("shows the success state when the server confirms delivery", async ({ page }) => {
    await page.route("**/api/contact", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ok: true, delivery: "email", reference: "e2e-ref" }),
      }),
    );
    await page.goto("/contact");
    await fill(page);
    await page.getByRole("button", { name: "Send inquiry" }).click();
    await expect(page.getByText("Thank you — your inquiry was sent.")).toBeVisible();
    await expect(page.getByText("Ref: e2e-ref")).toBeVisible();
  });

  test("prefills the service from a service page link", async ({ page }) => {
    await page.goto("/services/ai-automation");
    await page.getByRole("link", { name: "Discuss your project" }).click();
    await expect(page.getByLabel("Service needed")).toHaveValue("ai-automation");
  });
});
