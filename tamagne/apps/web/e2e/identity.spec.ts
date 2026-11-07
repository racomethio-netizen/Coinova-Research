import { randomInt } from "node:crypto";
import { expect, request as pwRequest, test, type APIRequestContext, type Page } from "@playwright/test";
import { base32Decode, totpAt, totpStep } from "@tamagne/core";
import pg from "pg";

const API = "http://127.0.0.1:4100";
const WEB = "http://localhost:3100";
const DB = process.env.TEST_DATABASE_URL ?? "postgresql://postgres@127.0.0.1:54339/tamagne_test";
const fakePhone = () => `+999${String(randomInt(0, 1e9)).padStart(9, "0")}`;
const idPng = Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), Buffer.from("synthetic ID fixture")]);

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
  return (await page.getByTestId("tamagne-id").textContent())!.trim();
}

async function officer() {
  const api = await pwRequest.newContext({ baseURL: WEB, extraHTTPHeaders: { Origin: WEB } });
  const csrf = async () => {
    const c = (await api.storageState()).cookies.find((x) => x.name === "tamagne_csrf")?.value;
    return c ?? ((await (await api.get("/api/v1/auth/csrf")).json()) as { data: { csrfToken: string } }).data.csrfToken;
  };
  const post = async (path: string, data: object) => api.post(path, { data, headers: { "X-CSRF-Token": await csrf() } });
  const phone = fakePhone();
  const start = await post("/api/v1/auth/register", { phone, acceptTerms: true, acceptPrivacy: true });
  const { challengeId } = ((await start.json()) as { data: { challengeId: string } }).data;
  const userId = ((await (await post("/api/v1/auth/otp/verify", { challengeId, code: await code(api, phone) })).json()) as { data: { userId: string } }).data.userId;
  const db = new pg.Client({ connectionString: DB });
  await db.connect();
  await db.query(`insert into staff_role_assignments ("userId", role) values ($1, 'VERIFICATION_OFFICER')`, [userId]);
  await db.end();
  const { secret } = ((await (await post("/api/v1/auth/mfa/totp/enroll", {})).json()) as { data: { secret: string } }).data;
  expect((await post("/api/v1/auth/mfa/totp/confirm", { code: totpAt(base32Decode(secret), totpStep(new Date())) })).status()).toBe(200);
  return { api, post };
}

test("Fayda (sandbox TEST provider): full browser redirect round trip, clearly labelled as a test", async ({ page }) => {
  const tamagneId = await registerSeller(page, "Fixture Fayda Shop");
  await page.getByRole("link", { name: "Identity verification" }).click();
  await expect(page.getByTestId("fayda-card")).toContainText("Test environment");
  await expect(page.getByRole("button", { name: "Verify with Fayda" })).toBeDisabled(); // consent first
  await page.getByLabel(/I agree that TAMAGNE may receive my identity confirmation/).check();
  await page.getByRole("button", { name: "Verify with Fayda" }).click();

  // Browser goes to the provider, which redirects back to /api/v1/fayda/callback (cross-site),
  // then to the identity page with the result.
  await page.waitForURL(/\/en\/dashboard\/identity\?fayda=verified/);
  await expect(page.getByTestId("fayda-result")).toHaveText("Sandbox test completed. This is NOT a real Fayda verification.");
  await expect(page.getByTestId("fayda-card")).toContainText("sandbox test");
  await expect(page.getByTestId("fayda-card")).toContainText("Verified");

  // The public page never presents a sandbox result as real verification.
  await page.goto(`/en/verify/${tamagneId}`);
  await expect(page.getByTestId("verify-faydaIdentity")).toContainText("sandbox test");
});

test("manual identity review: seller uploads an ID, reviewer approves; never shown as Fayda", async ({ page }) => {
  const tamagneId = await registerSeller(page, "Fixture Manual Shop");
  await page.goto("/en/dashboard/identity");
  await page.getByTestId("manual-card").getByRole("button", { name: "I agree" }).click();
  await page.getByLabel("Document").selectOption("KEBELE_ID");
  await page.getByLabel(/^Photo or scan/).setInputFiles({ name: "id.png", mimeType: "image/png", buffer: idPng });
  await page.getByRole("button", { name: "Submit for review" }).click();
  await expect(page.getByTestId("manual-status")).toContainText("Waiting for review");

  const rev = await officer();
  const queue = (await (await rev.api.get("/api/v1/admin/reviews/identity")).json()) as { data: { id: string; seller: { tamagneId: string } }[] };
  const item = queue.data.find((r) => r.seller.tamagneId === tamagneId)!;
  expect((await rev.post(`/api/v1/admin/identity-reviews/${item.id}/decision`, { decision: "APPROVE", reason: "Document genuine (fixture)" })).status()).toBe(200);
  await rev.api.dispose();

  await page.reload();
  await expect(page.getByTestId("manual-card")).toContainText("Verified");
  await expect(page.getByTestId("fayda-card")).toContainText("Not verified");
  await page.goto(`/en/verify/${tamagneId}`);
  await expect(page.getByTestId("verify-identityManualReview")).toContainText("Verified");
  await expect(page.getByTestId("verify-faydaIdentity")).toContainText("Not verified");
});
