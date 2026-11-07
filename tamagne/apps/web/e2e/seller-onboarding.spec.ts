import { randomInt } from "node:crypto";
import { expect, test, type Page } from "@playwright/test";

const API = "http://127.0.0.1:4100";
/** Synthetic +999 numbers only (unassigned country code). */
const fakePhone = () => `+999${String(randomInt(0, 1e9)).padStart(9, "0")}`;

async function latestCode(page: Page, phone: string): Promise<string> {
  const res = await page.request.get(`${API}/api/v1/test/sms-outbox/latest-code?phone=${encodeURIComponent(phone)}`);
  expect(res.ok()).toBeTruthy();
  return ((await res.json()) as { data: { code: string } }).data.code;
}

async function noHorizontalScroll(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
}

test("a seller registers by phone, sets up a profile and sees Level 1 only", async ({ page }) => {
  const phone = fakePhone();
  await page.goto("/en");
  await page.getByRole("link", { name: "Register" }).first().click();
  await expect(page.getByRole("heading", { name: "Create your seller account" })).toBeVisible();
  await noHorizontalScroll(page);

  await page.getByLabel("Mobile number").fill(phone);
  await page.getByRole("button", { name: "Send code" }).click();
  await expect(page.getByText("You need to accept both to continue.")).toBeVisible();

  await page.getByLabel("I agree to the Terms of Service").check();
  await page.getByLabel(/I have read the Privacy Policy/).check();
  await page.getByRole("button", { name: "Send code" }).click();
  await expect(page.getByLabel("6-digit code")).toBeVisible();

  const code = await latestCode(page, phone);
  await page.getByLabel("6-digit code").fill(code === "000000" ? "111111" : "000000");
  await page.getByRole("button", { name: "Verify and continue" }).click();
  await expect(page.getByText("That code is incorrect or has expired.")).toBeVisible();
  await expect(page.getByLabel("6-digit code")).toHaveAttribute("aria-invalid", "true");

  await page.getByLabel("6-digit code").fill(code);
  await page.getByRole("button", { name: "Verify and continue" }).click();
  await expect(page).toHaveURL(/\/en\/dashboard\/setup$/);
  await noHorizontalScroll(page);

  await page.getByLabel("Shop or seller name").fill("Fixture Telegram Shop");
  await page.getByLabel("What kind of seller are you?").selectOption("TELEGRAM_SHOP");
  await page.getByLabel("City (optional)").fill("Addis Ababa");
  await page.getByRole("button", { name: "Create profile" }).click();

  await expect(page).toHaveURL(/\/en\/dashboard$/);
  await expect(page.getByRole("heading", { name: "Fixture Telegram Shop" })).toBeVisible();
  await expect(page.getByTestId("tamagne-id")).toHaveText(/^TMG-[0-9A-HJKMNP-TV-Z]{10}$/);
  await expect(page.getByTestId("verification-phone")).toContainText("Verified");
  await expect(page.getByTestId("verification-faydaIdentity")).toContainText("Not verified");
  await expect(page.getByTestId("verification-business")).toContainText("Not verified");
  await noHorizontalScroll(page);

  await expect(page.getByTestId("public-state")).toHaveText("Your profile is hidden from buyers.");
  await page.getByRole("button", { name: "Show my profile to buyers" }).click();
  await expect(page.getByTestId("public-state")).toHaveText("Your profile can be found by buyers.");
  await page.getByRole("button", { name: "Hide my profile" }).click();
  await expect(page.getByTestId("public-state")).toHaveText("Your profile is hidden from buyers.");

  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/en$/);
  await page.goto("/en/dashboard");
  await expect(page).toHaveURL(/\/en\/login$/);
});

test("Amharic sign-up page renders in Ethiopic and validates accessibly", async ({ page }) => {
  await page.goto("/am/register");
  await expect(page.locator("html")).toHaveAttribute("lang", "am");
  await expect(page.getByRole("heading", { name: "የሻጭ መለያ ይፍጠሩ" })).toBeVisible();
  await page.getByLabel("የሞባይል ቁጥር").fill("12345");
  await page.getByLabel("የአገልግሎት ውሉን እስማማለሁ").check();
  await page.getByLabel(/የግላዊነት ፖሊሲውን/).check();
  await page.getByRole("button", { name: "ኮድ ላክ" }).click();
  await expect(page.getByText("ትክክለኛ የኢትዮጵያ ሞባይል ቁጥር ያስገቡ።")).toBeVisible();
  await expect(page.getByLabel("የሞባይል ቁጥር")).toHaveAttribute("aria-invalid", "true");
  await noHorizontalScroll(page);
});

test("protected pages redirect signed-out visitors", async ({ page }) => {
  await page.goto("/en/dashboard/setup");
  await expect(page).toHaveURL(/\/en\/login$/);
});
