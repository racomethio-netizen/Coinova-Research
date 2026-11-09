import { existsSync } from "node:fs";
import { join } from "node:path";
import request from "supertest";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { PrivacyService } from "../src/privacy/privacy.service.js";
import { Client, fakePhone, staffClient, startApp, type TestHarness } from "./support.js";

let h: TestHarness & { storageDir: string };
beforeAll(async () => {
  h = await startApp();
});
afterAll(async () => {
  await h.close();
});
const DAY = 86_400_000;
const pdf = Buffer.from("%PDF-1.7\n% licence (synthetic fixture)\n%%EOF\n");

/** A seller with a public profile, QR code, business and an uploaded document. */
async function fullSeller() {
  const c = new Client(h);
  const phone = fakePhone();
  const { userId } = await c.signIn(phone);
  const s = (await c.post("/api/v1/sellers", { displayName: "Fixture Privacy Shop", sellerType: "REGISTERED_BUSINESS", city: "Bahir Dar" })).body.data;
  await c.post("/api/v1/consents", { purpose: "PUBLIC_PROFILE_DISPLAY" });
  await c.get("/api/v1/sellers/me/qr");
  await c.post("/api/v1/businesses", { legalName: "Fixture Privacy PLC", legalForm: "OTHER", tinNumber: String(1_000_000_000 + Math.floor(Math.random() * 8e9)) });
  await c.post("/api/v1/consents", { purpose: "BUSINESS_DOCUMENT_PROCESSING" });
  const up = await c.upload("/api/v1/businesses/documents", { type: "BUSINESS_LICENSE" }, { data: pdf, filename: "l.pdf", contentType: "application/pdf" });
  const doc = await h.db.businessDocument.findUniqueOrThrow({ where: { id: up.body.data.documents[0].id } });
  return { c, phone, userId, tamagneId: s.tamagneId as string, sellerId: s.id as string, docKey: doc.storageKey };
}
const fileExists = (key: string) => existsSync(join(h.storageDir, ...key.split("/")));

describe("right of access (export)", () => {
  it("downloads the user's data as JSON, records the request, and leaves out reports about them", async () => {
    const s = await fullSeller();
    await new Client(h).post("/api/v1/reports", { seller: s.tamagneId, category: "OTHER", description: "A report written by someone else about this seller (fixture)." });
    const res = await s.c.get("/api/v1/privacy/export").expect(200);
    expect(res.headers["content-disposition"]).toMatch(/^attachment; filename="tamagne-my-data-/);
    expect(res.headers["cache-control"]).toBe("no-store");
    const data = JSON.parse(res.text);
    expect(data.account.phone).toBe(s.phone);
    expect(data.sellerProfile).toMatchObject({ tamagneId: s.tamagneId, city: "Bahir Dar" });
    expect(data.business.tinNumber).toMatch(/^\d{10}$/);
    expect(data.business.documents[0]).toMatchObject({ type: "BUSINESS_LICENSE", scanStatus: "CLEAN" });
    expect(data.consents.map((c: { purpose: string }) => c.purpose)).toEqual(expect.arrayContaining(["TERMS_OF_SERVICE", "PRIVACY_POLICY", "PUBLIC_PROFILE_DISPLAY"]));
    expect(data.verificationBadges[0]).toMatchObject({ level: "PHONE", status: "ACTIVE" });
    expect(res.text).not.toContain("someone else about this seller");
    expect(res.text).not.toMatch(/subjectHash|codeHash|tokenHash|storageKey|ipHash/);
    const dsr = await h.db.dataSubjectRequest.findFirstOrThrow({ where: { userId: s.userId, type: "ACCESS" } });
    expect(dsr.status).toBe("COMPLETED");
    expect((await request(h.url).get("/api/v1/privacy/export")).status).toBe(401);
  });
});

describe("right to correction", () => {
  it("creates a request staff can progress and close with an outcome", async () => {
    const s = await fullSeller();
    expect((await s.c.post("/api/v1/privacy/requests/correction", { details: "short" })).status).toBe(422);
    const r = (await s.c.post("/api/v1/privacy/requests/correction", { details: "My shop name is spelled wrong in Amharic." })).body.data[0];
    expect(r).toMatchObject({ type: "CORRECTION", status: "RECEIVED" });
    expect(new Date(r.dueAt).getTime() - Date.now()).toBeGreaterThan(29 * DAY);
    const support = await staffClient(h, "SUPPORT_OFFICER");
    expect((await support.c.get("/api/v1/admin/privacy/requests")).body.data.map((x: { id: string }) => x.id)).toContain(r.id);
    expect((await support.c.patch(`/api/v1/admin/privacy/requests/${r.id}`, { status: "COMPLETED" })).body.error.code).toBe("outcome_required");
    await support.c.patch(`/api/v1/admin/privacy/requests/${r.id}`, { status: "COMPLETED", outcome: "Amharic name corrected." });
    expect((await s.c.get("/api/v1/privacy/requests")).body.data[0]).toMatchObject({ status: "COMPLETED", outcome: "Amharic name corrected." });
    const finance = await staffClient(h, "FINANCE_OFFICER");
    expect((await finance.c.get("/api/v1/admin/privacy/requests")).status).toBe(403);
  });
});

describe("right to deletion", () => {
  it("locks and hides the account immediately; support can cancel during the grace period", async () => {
    const s = await fullSeller();
    expect((await s.c.post("/api/v1/privacy/deletion", { confirm: "yes" })).status).toBe(422);
    const del = await s.c.post("/api/v1/privacy/deletion", { confirm: "DELETE" });
    expect(del.status).toBe(202);
    expect(new Date(del.body.data.scheduledFor).getTime() - Date.now()).toBeGreaterThan(13 * DAY);
    expect((await s.c.get("/api/v1/sellers/me")).status).toBe(401); // signed out everywhere
    expect((await request(h.url).get(`/api/v1/verify/${s.tamagneId}`)).status).toBe(404);
    expect(await h.db.qrToken.count({ where: { sellerProfileId: s.sellerId, status: "ACTIVE" } })).toBe(0);
    const before = h.sms.outbox.length;
    h.clock.now += 16 * 60_000;
    await new Client(h).post("/api/v1/auth/login", { phone: s.phone });
    expect(h.sms.outbox.length).toBe(before); // a pending-deletion account cannot sign in

    const support = await staffClient(h, "SUPPORT_OFFICER");
    const req = (await support.c.get("/api/v1/admin/privacy/requests")).body.data.find((x: { seller: { tamagneId: string } | null }) => x.seller?.tamagneId === s.tamagneId);
    await support.c.patch(`/api/v1/admin/privacy/requests/${req.id}`, { status: "REJECTED", outcome: "Seller asked to cancel by phone; identity confirmed." });
    expect((await h.db.user.findUniqueOrThrow({ where: { id: s.userId } })).status).toBe("ACTIVE");
    expect((await new Client(h).signIn(s.phone, "login")).userId).toBe(s.userId);
  });

  it("after the grace period the job erases personal data, deletes files, keeps legal records, and frees the number", async () => {
    const s = await fullSeller();
    const badge = await h.db.sellerBadge.findFirstOrThrow({ where: { sellerProfileId: s.sellerId, level: "PHONE" } });
    expect(fileExists(s.docKey)).toBe(true);
    await s.c.post("/api/v1/privacy/deletion", { confirm: "DELETE" });

    const jobs = h.app.get(PrivacyService);
    expect((await jobs.runDeletionJob(new Date())).deleted).toBe(0); // grace period not over
    const r = await jobs.runDeletionJob(new Date(Date.now() + 15 * DAY));
    expect(r.deleted).toBeGreaterThanOrEqual(1);

    const user = await h.db.user.findUniqueOrThrow({ where: { id: s.userId } });
    expect(user).toMatchObject({ status: "DELETED", phoneE164: null, email: null });
    const seller = await h.db.sellerProfile.findUniqueOrThrow({ where: { id: s.sellerId } });
    expect(seller).toMatchObject({ displayName: "Deleted seller", city: null, isPublic: false, accountStatus: "DEACTIVATED" });
    expect(seller.deletedAt).not.toBeNull();
    const biz = await h.db.business.findUniqueOrThrow({ where: { sellerProfileId: s.sellerId } });
    expect(biz).toMatchObject({ legalName: "Deleted business", tinNumber: null });
    expect(fileExists(s.docKey)).toBe(false);
    expect((await h.db.sellerBadge.findUniqueOrThrow({ where: { id: badge.id } })).status).toBe("REVOKED");
    const otp = await h.db.phoneOtpChallenge.findUniqueOrThrow({ where: { id: badge.phoneOtpChallengeId! } });
    expect(otp.phoneE164).toBeNull(); // evidence kept, phone scrubbed
    expect(await h.db.phoneOtpChallenge.count({ where: { phoneE164: s.phone } })).toBe(0);
    expect(await h.db.notification.count({ where: { userId: s.userId } })).toBe(0);
    // Consent evidence and the audit trail are kept.
    expect(await h.db.consentRecord.count({ where: { userId: s.userId, status: "GRANTED" } })).toBe(0);
    expect(await h.db.consentRecord.count({ where: { userId: s.userId } })).toBeGreaterThan(0);
    const chain = await h.db.$queryRaw<{ broken: bigint | null }[]>`select public.audit_log_verify_chain() as broken`;
    expect(chain[0]!.broken).toBeNull();
    const dsr = await h.db.dataSubjectRequest.findFirstOrThrow({ where: { userId: s.userId, type: "DELETION" } });
    expect(dsr.status).toBe("COMPLETED");
    // Nothing identifying is left in the database for this phone number.
    const leftovers = await h.db.$queryRaw<{ n: bigint }[]>`
      select count(*) as n from users where "phoneE164" = ${s.phone}`;
    expect(Number(leftovers[0]!.n)).toBe(0);
    expect((await request(h.url).get(`/api/v1/verify/${s.tamagneId}`)).status).toBe(404);
    // The number can register again as a brand-new account.
    h.clock.now += 16 * 60_000;
    const again = await new Client(h).signIn(s.phone);
    expect(again.isNew).toBe(true);
    expect(again.userId).not.toBe(s.userId);
    // Idempotent.
    expect((await jobs.runDeletionJob(new Date(Date.now() + 15 * DAY))).deleted).toBe(0);
  });

  it("staff accounts cannot self-delete", async () => {
    const staff = await staffClient(h, "SUPPORT_OFFICER");
    const res = await staff.c.post("/api/v1/privacy/deletion", { confirm: "DELETE" });
    expect(res.body.error.code).toBe("staff_account");
  });
});

describe("retention job", () => {
  it("purges expired files and stale records, keeping evidence", async () => {
    const s = await fullSeller();
    const jobs = h.app.get(PrivacyService);
    const doc = await h.db.businessDocument.findFirstOrThrow({ where: { storageKey: s.docKey } });
    await h.db.businessDocument.update({ where: { id: doc.id }, data: { retainUntil: new Date(Date.now() - DAY) } });
    const badge = await h.db.sellerBadge.findFirstOrThrow({ where: { sellerProfileId: s.sellerId, level: "PHONE" } });
    const stale = await h.db.phoneOtpChallenge.create({ data: { phoneE164: fakePhone(), purpose: "LOGIN", codeHash: "a".repeat(64), expiresAt: new Date(Date.now() + 60_000) } });
    await h.db.$executeRaw`update phone_otp_challenges set "createdAt" = now() - interval '40 days', "expiresAt" = now() - interval '40 days' + interval '5 minutes' where id in (${stale.id}::uuid, ${badge.phoneOtpChallengeId!}::uuid)`;
    await h.db.notification.create({ data: { userId: s.userId, channel: "IN_APP", templateKey: "badge.expiring", locale: "EN", createdAt: new Date(Date.now() - 400 * DAY) } });

    const audited = () => h.db.auditLog.count({ where: { action: "job.retention" } });
    const auditsBefore = await audited();
    const r = await jobs.runRetentionJob();
    expect(await audited()).toBe(auditsBefore + 1); // the job leaves an audit trail
    expect(r.businessFiles).toBeGreaterThanOrEqual(1);
    expect(fileExists(s.docKey)).toBe(false);
    expect((await h.db.businessDocument.findUniqueOrThrow({ where: { id: doc.id } })).purgedAt).not.toBeNull();
    expect(await h.db.phoneOtpChallenge.count({ where: { id: stale.id } })).toBe(0);
    expect(await h.db.phoneOtpChallenge.count({ where: { id: badge.phoneOtpChallengeId! } })).toBe(1); // badge evidence kept
    expect(await h.db.notification.count({ where: { userId: s.userId, createdAt: { lt: new Date(Date.now() - 365 * DAY) } } })).toBe(0);
    const again = await jobs.runRetentionJob();
    expect(again.businessFiles).toBe(0);
  });
});
