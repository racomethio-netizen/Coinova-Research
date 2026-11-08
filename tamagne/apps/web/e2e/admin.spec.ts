import { randomInt } from "node:crypto";
import { expect, test, type Browser, type Page } from "@playwright/test";
import { base32Decode, totpAt, totpStep } from "@tamagne/core";
import pg from "pg";

const API = "http://127.0.0.1:4100";
const WEB = "http://localhost:3100";
const DB = process.env.TEST_DATABASE_URL ?? "postgresql://postgres@127.0.0.1:54339/tamagne_test";
const fakePhone = () => `+999${String(randomInt(0, 1e9)).padStart(9, "0")}`;
const pdf = Buffer.from("%PDF-1.7\n% business licence (synthetic fixture)\n%%EOF\n");

/** Click an item in the admin side navigation (exact name, scoped to the menu). */
const nav = (page: Page, name: string) => page.getByRole("navigation", { name: "Admin" }).getByRole("link", { name, exact: true });

async function csrf(page: Page) {
  const c = (await page.context().cookies()).find((x) => x.name === "tamagne_csrf")?.value;
  return c ?? ((await (await page.request.get(`${WEB}/api/v1/auth/csrf`)).json()) as { data: { csrfToken: string } }).data.csrfToken;
}
async function post(page: Page, path: string, data: object) {
  return page.request.post(`${WEB}/api/v1${path}`, { data, headers: { Origin: WEB, "X-CSRF-Token": await csrf(page) } });
}
async function code(page: Page, phone: string) {
  return ((await (await page.request.get(`${API}/api/v1/test/sms-outbox/latest-code?phone=${encodeURIComponent(phone)}`)).json()) as { data: { code: string } }).data.code;
}
async function registerViaApi(page: Page) {
  const phone = fakePhone();
  const start = await post(page, "/auth/register", { phone, acceptTerms: true, acceptPrivacy: true });
  const { challengeId } = ((await start.json()) as { data: { challengeId: string } }).data;
  const v = await post(page, "/auth/otp/verify", { challengeId, code: await code(page, phone) });
  return { phone, userId: ((await v.json()) as { data: { userId: string } }).data.userId };
}

/** A staff member in their own browser context, signed in through the UI with MFA enrolment. */
async function staffPage(browser: Browser, role: string) {
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  // Staff accounts are ordinary accounts with a role. Create one directly (synthetic +999 number),
  // then sign in through the normal login page: staff land in the console.
  const phone = fakePhone();
  const db = new pg.Client({ connectionString: DB });
  await db.connect();
  const { rows } = await db.query(`insert into users ("phoneE164", status, "phoneVerifiedAt", "updatedAt") values ($1, 'ACTIVE', now(), now()) returning id`, [phone]);
  const userId = rows[0].id as string;
  await db.query(`insert into staff_role_assignments ("userId", role) values ($1, $2)`, [userId, role]);
  await db.end();
  await page.goto("/en/login");
  await page.getByLabel("Mobile number").fill(phone);
  await page.getByRole("button", { name: "Send code" }).click();
  await expect(page.getByLabel("6-digit code")).toBeVisible();
  await page.getByLabel("6-digit code").fill(await code(page, phone));
  await page.getByRole("button", { name: "Verify and continue" }).click();
  await page.waitForURL(/\/en\/admin$/);
  await expect(page.getByRole("heading", { name: "Two-factor authentication" })).toBeVisible();
  await page.getByRole("button", { name: "Set up authenticator app" }).click();
  const secret = (await page.getByTestId("totp-secret").textContent())!.trim();
  await page.getByLabel("Authenticator code").fill(totpAt(base32Decode(secret), totpStep(new Date())));
  await page.getByRole("button", { name: "Confirm" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  return { page, ctx, userId };
}

/** A seller with a public profile and a submitted business, created through the API. */
async function sellerWithSubmittedBusiness(browser: Browser, name: string) {
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  await page.goto("/en");
  await registerViaApi(page);
  const s = await post(page, "/sellers", { displayName: name, sellerType: "REGISTERED_BUSINESS" });
  const tamagneId = ((await s.json()) as { data: { tamagneId: string } }).data.tamagneId;
  await post(page, "/consents", { purpose: "PUBLIC_PROFILE_DISPLAY" });
  await post(page, "/businesses", { legalName: `${name} PLC`, legalForm: "PRIVATE_LIMITED_COMPANY" });
  await post(page, "/consents", { purpose: "BUSINESS_DOCUMENT_PROCESSING" });
  const up = await page.request.post(`${WEB}/api/v1/businesses/documents`, {
    headers: { Origin: WEB, "X-CSRF-Token": await csrf(page) },
    multipart: { type: "BUSINESS_LICENSE", file: { name: "licence.pdf", mimeType: "application/pdf", buffer: pdf } },
  });
  expect(up.status()).toBe(201);
  expect((await post(page, "/businesses/me/submit", {})).status()).toBe(200);
  return { page, ctx, tamagneId };
}

test("non-staff are kept out of the console", async ({ page }) => {
  await page.goto("/en");
  await registerViaApi(page);
  await page.goto("/en/admin");
  await expect(page.getByRole("heading", { name: "Staff only" })).toBeVisible();
});

test("a verification officer reviews and approves a business in the console", async ({ browser }) => {
  const seller = await sellerWithSubmittedBusiness(browser, "Fixture Console Trading");
  const officer = await staffPage(browser, "VERIFICATION_OFFICER");
  const p = officer.page;
  await expect(nav(p, "Staff")).toHaveCount(0); // role-based navigation
  await nav(p, "Business reviews").click();
  await p.getByRole("row", { name: new RegExp(seller.tamagneId) }).getByRole("link", { name: "Review" }).click();
  await expect(p.getByRole("link", { name: "Download" })).toBeVisible();
  const dl = await p.request.get(`${WEB}${await p.getByRole("link", { name: "Download" }).getAttribute("href")}`);
  expect(dl.headers()["content-disposition"]).toMatch(/^attachment/);
  await p.getByLabel("Reason (shown to the seller if not approved)").fill("Licence valid (fixture)");
  await p.getByRole("button", { name: "Record decision" }).click();
  await expect(p.getByTestId("review-status")).toHaveText("Approved. The Level 3 business badge has been issued.");

  await seller.page.goto(`/en/verify/${seller.tamagneId}`);
  await expect(seller.page.getByTestId("verify-business")).toContainText("Verified");
  await officer.ctx.close();
  await seller.ctx.close();
});

test("report → suspension → appeal → overturned → second reviewer reactivates", async ({ browser, page: buyer }) => {
  const seller = await sellerWithSubmittedBusiness(browser, "Fixture Appeal Shop");

  // A buyer reports the seller from the public result page.
  await buyer.goto(`/en/verify/${seller.tamagneId}`);
  await buyer.getByRole("link", { name: "Report this seller" }).click();
  await expect(buyer.getByLabel("Seller's Tamagne ID")).toHaveValue(seller.tamagneId);
  await buyer.getByLabel("What happened?").selectOption("NON_DELIVERY");
  await buyer.getByLabel(/Describe what happened/).fill("Paid 1,500 birr on 2 Nov; seller stopped answering (fixture).");
  await buyer.getByRole("button", { name: "Send report" }).click();
  await expect(buyer.getByTestId("report-thanks")).toBeVisible();

  // Security officer investigates, closes the report and suspends the seller.
  const sec = await staffPage(browser, "SECURITY_OFFICER");
  await nav(sec.page, "Fraud reports").click();
  await sec.page.getByTestId("report-item").filter({ hasText: seller.tamagneId }).getByRole("link", { name: "Open" }).click();
  await expect(sec.page.getByTestId("report-description")).toContainText("1,500 birr");
  await sec.page.getByLabel("New status").selectOption("INVESTIGATING");
  await sec.page.getByRole("button", { name: "Save" }).click();
  await expect(sec.page.getByLabel("New status")).toContainText("action taken");
  await sec.page.getByLabel("New status").selectOption("ACTION_TAKEN");
  await sec.page.getByLabel("Note (required when closing)").fill("Seller suspended pending appeal");
  await sec.page.getByRole("button", { name: "Save" }).click();
  await expect(sec.page.getByText("Resolution: Seller suspended pending appeal")).toBeVisible();
  await sec.page.goto(`/en/admin/sellers/${seller.tamagneId}`);
  sec.page.once("dialog", (d) => d.accept());
  await sec.page.getByTestId("suspend-form").getByLabel("Reason").fill("Confirmed non-delivery report");
  await sec.page.getByTestId("suspend-form").getByRole("button", { name: "Suspend" }).click();
  await expect(sec.page.getByText(/Suspended .*: Confirmed non-delivery report/)).toBeVisible();

  await buyer.goto(`/en/verify/${seller.tamagneId}`);
  await expect(buyer.getByTestId("verify-account-status")).toHaveText("Suspended");

  // The seller sees the notification and appeals.
  await seller.page.goto("/en/dashboard");
  await expect(seller.page.getByTestId("notifications")).toContainText("Your seller account has been suspended");
  await seller.page.getByRole("link", { name: "Appeals" }).first().click();
  await seller.page.getByLabel("What is this about?").selectOption("ACCOUNT_SUSPENSION");
  await seller.page.getByLabel(/Explain why/).fill("The buyer received the order on 6 Nov; I have the courier receipt.");
  await seller.page.getByRole("button", { name: "Submit appeal" }).click();
  await expect(seller.page.getByTestId("my-appeals")).toContainText("Received");

  // A support officer (not the suspender) overturns; a Super Admin confirms the reactivation.
  const support = await staffPage(browser, "SUPPORT_OFFICER");
  await nav(support.page, "Appeals").click();
  const item = support.page.getByTestId("appeal-item").filter({ hasText: seller.tamagneId });
  await item.getByLabel("Outcome").selectOption("OVERTURNED");
  await item.getByLabel("Note to the seller").fill("Courier receipt confirms delivery");
  await item.getByRole("button", { name: "Decide" }).click();
  await expect(support.page.getByTestId("appeal-item").filter({ hasText: seller.tamagneId })).toHaveCount(0);
  await buyer.reload();
  await expect(buyer.getByTestId("verify-account-status")).toHaveText("Suspended"); // still needs a second reviewer

  const admin = await staffPage(browser, "SUPER_ADMIN");
  await nav(admin.page, "Second reviews").click();
  const review = admin.page.getByTestId("second-review-item").filter({ hasText: seller.tamagneId });
  await review.getByLabel("Your decision").selectOption("APPROVE");
  await review.getByLabel("Reason").fill("Receipt checked");
  await review.getByRole("button", { name: "Record second decision" }).click();
  await expect(admin.page.getByTestId("second-review-item").filter({ hasText: seller.tamagneId })).toHaveCount(0);

  await buyer.reload();
  await expect(buyer.getByTestId("verify-account-status")).toHaveText("Active");
  await seller.page.goto("/en/dashboard/appeals");
  await expect(seller.page.getByTestId("my-appeals")).toContainText("Decision overturned");

  // Audit trail is intact.
  await admin.page.goto("/en/admin/audit?verify=1");
  await expect(admin.page.getByTestId("chain-result")).toContainText("Hash chain intact");
  for (const x of [sec, support, admin, seller]) await x.ctx.close();
});

test("the report form works in Amharic", async ({ page }) => {
  await page.goto("/am/report?seller=TMG-0000000000");
  await expect(page.getByRole("heading", { name: "ሻጭ ሪፖርት ያድርጉ" })).toBeVisible();
  await page.getByLabel(/የተፈጠረውን ይግለጹ/).fill("አጭር");
  await page.getByRole("button", { name: "ሪፖርት ላክ" }).click();
  await expect(page.getByText(/መግለጫው ቢያንስ 20 ፊደላት/)).toBeVisible();
  await page.getByLabel(/የተፈጠረውን ይግለጹ/).fill("ከፍያለሁ ግን ምንም አልደረሰኝም፤ ሻጩ መልስ አይሰጥም። (የሙከራ)");
  await page.getByRole("button", { name: "ሪፖርት ላክ" }).click();
  await expect(page.getByTestId("report-thanks")).toBeVisible();
});
