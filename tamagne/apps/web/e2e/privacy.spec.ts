import { randomInt } from "node:crypto";
import { readFileSync } from "node:fs";
import { expect, test, type Page } from "@playwright/test";

const API = "http://127.0.0.1:4100";
const fakePhone = () => `+999${String(randomInt(0, 1e9)).padStart(9, "0")}`;

async function registerSeller(page: Page) {
  const phone = fakePhone();
  await page.goto("/en/register");
  // Policy texts are reachable from the sign-up form.
  await expect(page.getByRole("link", { name: "Read the Privacy Policy" })).toHaveAttribute("href", "/en/privacy");
  await page.getByLabel("Mobile number").fill(phone);
  await page.getByLabel("I agree to the Terms of Service").check();
  await page.getByLabel(/I have read the Privacy Policy/).check();
  await page.getByRole("button", { name: "Send code" }).click();
  await expect(page.getByLabel("6-digit code")).toBeVisible();
  const code = ((await (await page.request.get(`${API}/api/v1/test/sms-outbox/latest-code?phone=${encodeURIComponent(phone)}`)).json()) as { data: { code: string } }).data.code;
  await page.getByLabel("6-digit code").fill(code);
  await page.getByRole("button", { name: "Verify and continue" }).click();
  await page.waitForURL(/setup$/);
  await page.getByLabel("Shop or seller name").fill("Fixture Privacy Shop");
  await page.getByRole("button", { name: "Create profile" }).click();
  await page.waitForURL(/dashboard$/);
  await page.getByRole("button", { name: "Show my profile to buyers" }).click();
  await expect(page.getByTestId("qr-image")).toBeVisible();
  return { phone, tamagneId: (await page.getByTestId("tamagne-id").textContent())!.trim() };
}

test("policy pages render, marked as drafts; Amharic shows the translation notice", async ({ page }) => {
  await page.goto("/en/privacy");
  await expect(page.getByRole("heading", { name: "Privacy Policy", level: 1 })).toBeVisible();
  await expect(page.getByText("We do not receive or store your Fayda number")).toBeVisible();
  await expect(page.getByText(/This text is a draft/)).toBeVisible();
  await page.goto("/am/terms");
  await expect(page.getByText("የዚህ ሰነድ የአማርኛ ትርጉም በዝግጅት ላይ ነው።", { exact: false })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Terms of Service", level: 1 })).toBeVisible();
});

test("a seller manages consents, downloads their data, requests a correction and deletes the account", async ({ page }) => {
  const s = await registerSeller(page);
  await page.getByRole("link", { name: "Privacy and your data" }).click();
  await expect(page.getByRole("heading", { name: "Privacy and your data" })).toBeVisible();

  // Consents: required ones can't be withdrawn here; public display can.
  const consents = page.getByTestId("consents");
  await expect(consents.getByRole("listitem").filter({ hasText: "Terms of Service" })).toContainText("Required for your account");
  const publicItem = consents.getByRole("listitem").filter({ hasText: "Show my profile to buyers" });
  await expect(publicItem).toContainText("Given");
  await publicItem.getByRole("button", { name: "Withdraw" }).click();
  await expect(publicItem).toContainText("Withdrawn");
  await page.goto(`/en/verify/${s.tamagneId}`);
  await expect(page.getByTestId("verify-not-found")).toBeVisible();
  await page.goBack();

  // Export.
  const downloadPromise = page.waitForEvent("download");
  await page.getByTestId("export-link").click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/^tamagne-my-data-\d{4}-\d{2}-\d{2}\.json$/);
  const data = JSON.parse(readFileSync((await download.path())!, "utf8"));
  expect(data.account.phone).toBe(s.phone);
  expect(data.sellerProfile.tamagneId).toBe(s.tamagneId);

  // Correction request.
  await page.getByLabel(/What should be corrected/).fill("My shop name has a typo in it.");
  await page.getByRole("button", { name: "Send correction request" }).click();
  await expect(page.getByTestId("privacy-requests")).toContainText("Correction");
  await expect(page.getByTestId("privacy-requests")).toContainText("Data download");

  // Deletion.
  await expect(page.getByRole("button", { name: "Delete my account" })).toBeDisabled();
  await page.getByLabel("Type DELETE to confirm").fill("DELETE");
  await page.getByRole("button", { name: "Delete my account" }).click();
  await expect(page.getByTestId("deletion-done")).toBeVisible();
  await page.goto("/en/dashboard");
  await expect(page).toHaveURL(/\/en\/login$/); // signed out everywhere
});
