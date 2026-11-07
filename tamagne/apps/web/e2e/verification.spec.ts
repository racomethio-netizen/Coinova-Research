import { randomInt } from "node:crypto";
import { expect, test, type Browser, type Page } from "@playwright/test";

const API = "http://127.0.0.1:4100";
const fakePhone = () => `+999${String(randomInt(0, 1e9)).padStart(9, "0")}`;

async function latestCode(page: Page, phone: string) {
  const res = await page.request.get(`${API}/api/v1/test/sms-outbox/latest-code?phone=${encodeURIComponent(phone)}`);
  return ((await res.json()) as { data: { code: string } }).data.code;
}

/** Registers a seller through the UI and makes the profile public; returns the seller's page. */
async function publicSeller(browser: Browser, name: string) {
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  const phone = fakePhone();
  await page.goto("/en/register");
  await page.getByLabel("Mobile number").fill(phone);
  await page.getByLabel("I agree to the Terms of Service").check();
  await page.getByLabel(/I have read the Privacy Policy/).check();
  await page.getByRole("button", { name: "Send code" }).click();
  await expect(page.getByLabel("6-digit code")).toBeVisible(); // code has been sent
  await page.getByLabel("6-digit code").fill(await latestCode(page, phone));
  await page.getByRole("button", { name: "Verify and continue" }).click();
  await page.waitForURL(/setup$/);
  await page.getByLabel("Shop or seller name").fill(name);
  await page.getByLabel("Name in Amharic (optional)").fill("የሙከራ ሱቅ");
  await page.getByRole("button", { name: "Create profile" }).click();
  await page.waitForURL(/dashboard$/);
  await expect(page.getByTestId("qr-card")).toContainText("Make your profile public to get a QR code");
  await page.getByRole("button", { name: "Show my profile to buyers" }).click();
  await expect(page.getByTestId("qr-image")).toBeVisible();
  return { ctx, page, tamagneId: (await page.getByTestId("tamagne-id").textContent())!.trim() };
}

test("a buyer scans a seller's QR code and sees live status; a replaced code stops working", async ({ browser, page }) => {
  const seller = await publicSeller(browser, "Fixture QR Shop");
  const qrUrl = (await seller.page.getByTestId("qr-url").textContent())!.trim();
  const path = new URL(qrUrl).pathname; // /verify/<token>, language-neutral

  // Buyer (separate, signed-out browser) scans the code.
  await page.goto(path);
  await expect(page).toHaveURL(/\/en\/verify\//);
  const result = page.getByTestId("verify-result");
  await expect(result).toContainText("Fixture QR Shop");
  await expect(page.getByTestId("verify-tamagne-id")).toHaveText(seller.tamagneId);
  await expect(result).toContainText("This QR code is valid");
  await expect(page.getByTestId("verify-phone")).toContainText("Verified");
  await expect(page.getByTestId("verify-faydaIdentity")).toContainText("Not verified");
  await expect(page.getByTestId("verify-business")).toContainText("Not verified");
  await expect(page.getByText("not a government institution")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await expect(page.locator('meta[name="robots"]')).toHaveAttribute("content", /noindex/);

  // Seller replaces the code.
  seller.page.once("dialog", (d) => d.accept());
  await seller.page.getByRole("button", { name: "Replace code" }).click();
  await expect(seller.page.getByTestId("qr-url")).not.toHaveText(qrUrl);

  await page.goto(path);
  await expect(page.getByTestId("verify-revoked")).toContainText("This QR code is no longer valid");
  await seller.ctx.close();
});

test("a buyer looks up a Tamagne ID from the home page, typed carelessly", async ({ browser, page }) => {
  const seller = await publicSeller(browser, "Fixture Lookup Shop");
  await page.goto("/en");
  await page.getByLabel("Tamagne ID").fill(` ${seller.tamagneId.toLowerCase().replace("-", "")} `);
  await page.getByRole("button", { name: "Check" }).click();
  await expect(page.getByTestId("verify-result")).toContainText("Fixture Lookup Shop");
  await expect(page.getByTestId("verify-account-status")).toHaveText("Active");

  // Hiding the profile makes it unfindable.
  await seller.page.getByRole("button", { name: "Hide my profile" }).click();
  await expect(seller.page.getByTestId("public-state")).toHaveText("Your profile is hidden from buyers.");
  await page.reload();
  await expect(page.getByTestId("verify-not-found")).toBeVisible();
  await seller.ctx.close();
});

test("unknown IDs show a careful not-found message, in Amharic too", async ({ page }) => {
  await page.goto("/en/verify/TMG-0000000000");
  await expect(page.getByTestId("verify-not-found")).toContainText("No public seller found");
  await page.goto("/am/verify/TMG-0000000000");
  await expect(page.getByTestId("verify-not-found")).toContainText("ይፋዊ ሻጭ አልተገኘም");
});

test("the QR redirect honours an Amharic browser", async ({ browser }) => {
  const ctx = await browser.newContext({ locale: "am-ET", extraHTTPHeaders: { "Accept-Language": "am-ET,am;q=0.9,en;q=0.5" } });
  const page = await ctx.newPage();
  await page.goto("/verify/TMG-0000000000");
  await expect(page).toHaveURL(/\/am\/verify\/TMG-0000000000$/);
  await ctx.close();
});
