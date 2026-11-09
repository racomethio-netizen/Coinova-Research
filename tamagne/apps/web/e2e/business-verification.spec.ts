import { randomInt } from "node:crypto";
import { expect, request as pwRequest, test, type APIRequestContext, type Page } from "@playwright/test";
import { base32Decode, totpAt, totpStep } from "@tamagne/core";
import pg from "pg";

const API = "http://127.0.0.1:4100";
const WEB = "http://localhost:3100";
const DB = process.env.TEST_DATABASE_URL ?? "postgresql://postgres@127.0.0.1:54339/tamagne_test";
const fakePhone = () => `+999${String(randomInt(0, 1e9)).padStart(9, "0")}`;
const pdf = (label: string) => Buffer.from(`%PDF-1.7\n% ${label} (synthetic fixture)\n1 0 obj << /Type /Catalog >> endobj\n%%EOF\n`);
const EICAR = "X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*";

async function code(req: APIRequestContext, phone: string) {
  const res = await req.get(`${API}/api/v1/test/sms-outbox/latest-code?phone=${encodeURIComponent(phone)}`);
  return ((await res.json()) as { data: { code: string } }).data.code;
}

async function registerSeller(page: Page, name: string) {
  const phone = fakePhone();
  await page.goto("/en/register");
  await page.getByLabel("Mobile number").fill(phone);
  await page.getByLabel("I agree to the Terms of Service").check();
  await page.getByLabel(/I have read the Privacy Policy/).check();
  await page.getByRole("button", { name: "Send code" }).click();
  await expect(page.getByLabel("6-digit code")).toBeVisible();
  await page.getByLabel("6-digit code").fill(await code(page.request, phone));
  await page.getByRole("button", { name: "Verify and continue" }).click();
  await page.waitForURL(/setup$/);
  await page.getByLabel("Shop or seller name").fill(name);
  await page.getByRole("button", { name: "Create profile" }).click();
  await page.waitForURL(/dashboard$/);
  await page.getByRole("button", { name: "Show my profile to buyers" }).click();
  await expect(page.getByTestId("qr-image")).toBeVisible();
}

/** A verification officer signed in over the API (staff UI arrives in Phase 6). Role granted directly in the test DB. */
async function officer(): Promise<{ api: APIRequestContext; csrf: () => Promise<string> }> {
  const api = await pwRequest.newContext({ baseURL: WEB, extraHTTPHeaders: { Origin: WEB } });
  const csrf = async () => {
    const state = await api.storageState();
    const existing = state.cookies.find((c) => c.name === "tamagne_csrf")?.value;
    if (existing) return existing;
    return ((await (await api.get("/api/v1/auth/csrf")).json()) as { data: { csrfToken: string } }).data.csrfToken;
  };
  const post = async (path: string, data: object) => api.post(path, { data, headers: { "X-CSRF-Token": await csrf() } });
  const phone = fakePhone();
  const signIn = async () => {
    const start = await post("/api/v1/auth/register", { phone, acceptTerms: true, acceptPrivacy: true });
    expect(start.status()).toBe(202);
    const { challengeId } = ((await start.json()) as { data: { challengeId: string } }).data;
    return post("/api/v1/auth/otp/verify", { challengeId, code: await code(api, phone) });
  };
  const userId = ((await (await signIn()).json()) as { data: { userId: string } }).data.userId;
  const db = new pg.Client({ connectionString: DB });
  await db.connect();
  await db.query(`insert into staff_role_assignments ("userId", role) values ($1, 'VERIFICATION_OFFICER')`, [userId]);
  await db.end();
  // Roles are re-read on every request, so the current session can enrol MFA straight away.
  const enroll = await post("/api/v1/auth/mfa/totp/enroll", {});
  const { secret } = ((await enroll.json()) as { data: { secret: string } }).data;
  const confirm = await post("/api/v1/auth/mfa/totp/confirm", { code: totpAt(base32Decode(secret), totpStep(new Date())) });
  expect(confirm.status()).toBe(200);
  return { api, csrf };
}

test("a seller verifies their business and a reviewer approves it (Level 3)", async ({ page }) => {
  await registerSeller(page, "Fixture Business Shop");
  const tamagneId = (await page.getByTestId("tamagne-id").textContent())!.trim();
  await expect(page.getByTestId("verification-business")).toContainText("Not verified");

  await page.getByRole("link", { name: "Business verification" }).click();
  await expect(page.getByRole("heading", { name: "Business verification", level: 1 })).toBeVisible();
  await expect(page.getByTestId("business-status")).toHaveText("Not started");
  await page.getByLabel("Registered legal name").fill("Fixture Trading PLC");
  await page.getByLabel("Legal form").selectOption("PRIVATE_LIMITED_COMPANY");
  await page.getByLabel("TIN (10 digits, optional)").fill(String(randomInt(1_000_000_000, 9_999_999_999)));
  await page.getByRole("button", { name: "Save details" }).click();
  await expect(page.getByTestId("business-status")).toHaveText("Draft");

  await page.getByRole("button", { name: "I agree" }).click();
  await expect(page.getByTestId("consent-given")).toBeVisible();

  // An infected file is refused with a clear message and not listed.
  await page.getByLabel("Document type").selectOption("BUSINESS_LICENSE");
  await page.getByLabel(/^File/).setInputFiles({ name: "licence.pdf", mimeType: "application/pdf", buffer: Buffer.from(`%PDF-1.4\n${EICAR}`) });
  await page.getByRole("button", { name: "Upload" }).click();
  await expect(page.getByText("This file was flagged by our malware scanner and was not stored.")).toBeVisible();

  await page.getByLabel(/^File/).setInputFiles({ name: "licence.pdf", mimeType: "application/pdf", buffer: pdf("business licence") });
  await page.getByRole("button", { name: "Upload" }).click();
  await expect(page.getByTestId("documents")).toContainText("Business licence");
  await expect(page.getByTestId("documents")).toContainText("Scan passed");
  await expect(page.getByTestId("documents")).toContainText("Awaiting review");

  await page.getByRole("button", { name: "Submit for review" }).click();
  await expect(page.getByTestId("business-status")).toHaveText("Under review");
  await expect(page.getByLabel("Registered legal name")).toHaveCount(0); // locked

  // Reviewer approves.
  const rev = await officer();
  const queue = (await (await rev.api.get(`/api/v1/admin/reviews/businesses?seller=${tamagneId}`)).json()) as { data: { businesses: { id: string; seller: { tamagneId: string } }[] } };
  const item = queue.data.businesses.find((b) => b.seller.tamagneId === tamagneId)!;
  expect(item).toBeTruthy();
  const detail = (await (await rev.api.get(`/api/v1/admin/businesses/${item.id}`)).json()) as { data: { documents: { id: string }[] } };
  const decision = await rev.api.post(`/api/v1/admin/businesses/${item.id}/decision`, {
    headers: { "X-CSRF-Token": await rev.csrf() },
    data: { decision: "APPROVE", reason: "Licence valid (fixture)", documents: detail.data.documents.map((d) => ({ documentId: d.id, status: "APPROVED" })) },
  });
  expect(decision.status()).toBe(200);
  await rev.api.dispose();

  await page.reload();
  await expect(page.getByTestId("business-status")).toHaveText("Verified");
  await expect(page.getByText("Your business documents are verified.")).toBeVisible();
  await page.goto("/en/dashboard");
  await expect(page.getByTestId("verification-business")).toContainText("Verified");
  await expect(page.getByTestId("verification-faydaIdentity")).toContainText("Not verified");

  // Buyers see Level 3, but not the legal name or TIN.
  await page.goto(`/en/verify/${tamagneId}`);
  await expect(page.getByTestId("verify-business")).toContainText("Verified");
  await expect(page.getByTestId("verify-result")).not.toContainText("Fixture Trading PLC");
});
