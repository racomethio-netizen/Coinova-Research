import request from "supertest";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { OperationsService } from "../src/admin/operations.service.js";
import { Client, fakePhone, staffClient, startApp, type TestHarness } from "./support.js";

let h: TestHarness & { storageDir: string };
beforeAll(async () => {
  h = await startApp();
});
afterAll(async () => {
  await h.close();
});

async function seller(name = "Fixture Shop") {
  const c = new Client(h);
  const phone = fakePhone();
  const { userId } = await c.signIn(phone);
  const s = (await c.post("/api/v1/sellers", { displayName: name, sellerType: "TELEGRAM_SHOP" })).body.data;
  await c.post("/api/v1/consents", { purpose: "PUBLIC_PROFILE_DISPLAY" });
  return { c, phone, userId, tamagneId: s.tamagneId as string, sellerId: s.id as string };
}

const lookup = (id: string) => request(h.url).get(`/api/v1/verify/${id}`);

describe("fraud reports", () => {
  it("anyone can report; the response never reveals whether the seller exists", async () => {
    const s = await seller();
    const anon = new Client(h);
    const body = { category: "NON_DELIVERY", description: "Paid 2,000 birr by transfer; nothing delivered after 3 weeks (fixture)." };
    const real = await anon.post("/api/v1/reports", { ...body, seller: s.tamagneId, contact: "reporter@example.test" });
    const fake = await anon.post("/api/v1/reports", { ...body, seller: "TMG-0000000000" });
    expect(real.status).toBe(202);
    expect(fake.status).toBe(202);
    expect(real.body).toEqual({ data: { received: true } });
    expect(fake.body).toEqual(real.body);
    const row = await h.db.fraudReport.findFirstOrThrow({ where: { sellerProfileId: s.sellerId } });
    expect(row.reporterUserId).toBeNull();
    expect(Buffer.from(row.reporterContactEnc!).toString("latin1")).not.toContain("reporter@example.test");
    expect((await anon.post("/api/v1/reports", { ...body, seller: s.tamagneId, description: "too short" })).status).toBe(422);
  });

  it("investigators triage and resolve with a note; viewing contact details is audited; roles enforced", async () => {
    const s = await seller();
    await new Client(h).post("/api/v1/reports", { seller: s.tamagneId, category: "IMPERSONATION", description: "This shop copies a well-known brand's Telegram channel (fixture).", contact: "0911000000" });
    const report = await h.db.fraudReport.findFirstOrThrow({ where: { sellerProfileId: s.sellerId } });
    const officer = await staffClient(h, "SECURITY_OFFICER");
    const list = (await officer.c.get("/api/v1/admin/reports")).body.data;
    expect(list.find((r: { id: string }) => r.id === report.id)).toMatchObject({ category: "IMPERSONATION", status: "SUBMITTED", seller: { tamagneId: s.tamagneId } });
    const detail = (await officer.c.get(`/api/v1/admin/reports/${report.id}`)).body.data;
    expect(detail.reporterContact).toBe("0911000000");
    expect(await h.db.auditLog.count({ where: { action: "report.contact_viewed", targetId: report.id } })).toBe(1);

    expect((await officer.c.patch(`/api/v1/admin/reports/${report.id}`, { status: "INVESTIGATING", assignToMe: true })).body.data.status).toBe("INVESTIGATING");
    expect((await officer.c.patch(`/api/v1/admin/reports/${report.id}`, { status: "ACTION_TAKEN" })).body.error.code).toBe("note_required");
    const done = await officer.c.patch(`/api/v1/admin/reports/${report.id}`, { status: "ACTION_TAKEN", note: "Seller suspended pending appeal" });
    expect(done.body.data).toMatchObject({ status: "ACTION_TAKEN", resolutionNote: "Seller suspended pending appeal" });
    expect((await officer.c.patch(`/api/v1/admin/reports/${report.id}`, { status: "TRIAGED" })).body.error.code).toBe("invalid_transition");

    const verifier = await staffClient(h, "VERIFICATION_OFFICER");
    expect((await verifier.c.get("/api/v1/admin/reports")).status).toBe(403);
    expect((await s.c.get("/api/v1/admin/reports")).status).toBe(403);
  });
});

describe("suspension, reactivation (second reviewer) and appeals", () => {
  it("suspension is immediate and public; reactivation needs a different second reviewer", async () => {
    const s = await seller();
    const support = await staffClient(h, "SUPPORT_OFFICER");
    expect((await support.c.post(`/api/v1/admin/sellers/${s.tamagneId}/suspend`, { reason: "x" })).status).toBe(422);
    const res = await support.c.post(`/api/v1/admin/sellers/${s.tamagneId}/suspend`, { reason: "Multiple confirmed non-delivery reports" });
    expect(res.body.data.accountStatus).toBe("SUSPENDED");
    const pub = await lookup(s.tamagneId).expect(200);
    expect(pub.body.data.accountStatus).toBe("SUSPENDED");
    expect(pub.body.data.verification.phone.status).toBe("SUSPENDED");
    expect(JSON.stringify(pub.body)).not.toContain("non-delivery");

    // Support officers may suspend but not reactivate.
    expect((await support.c.post(`/api/v1/admin/sellers/${s.tamagneId}/reactivate`, { reason: "Resolved with buyer" })).status).toBe(403);
    const sec = await staffClient(h, "SECURITY_OFFICER");
    const req = await sec.c.post(`/api/v1/admin/sellers/${s.tamagneId}/reactivate`, { reason: "Seller refunded all buyers" });
    expect(req.body.data.status).toBe("PENDING_SECOND_REVIEW");
    expect((await lookup(s.tamagneId)).body.data.accountStatus).toBe("SUSPENDED");
    expect((await sec.c.post(`/api/v1/admin/reviews/${req.body.data.reviewId}/second-review`, { decision: "APPROVE", reason: "self" })).body.error.code).toBe("same_reviewer");
    const pending = (await sec.c.get("/api/v1/admin/reviews/second")).body.data;
    expect(pending.map((r: { id: string }) => r.id)).toContain(req.body.data.reviewId);

    const admin = await staffClient(h, "SUPER_ADMIN");
    const ok = await admin.c.post(`/api/v1/admin/reviews/${req.body.data.reviewId}/second-review`, { decision: "APPROVE", reason: "Refund evidence checked" });
    expect(ok.body.data.outcome).toBe("REACTIVATE");
    expect((await lookup(s.tamagneId)).body.data.accountStatus).toBe("ACTIVE");
    const notes = (await s.c.get("/api/v1/notifications")).body.data.map((n: { templateKey: string }) => n.templateKey);
    expect(notes).toEqual(expect.arrayContaining(["account.suspended", "account.reactivated"]));
  });

  it("a suspended seller appeals; the suspender may not decide; overturning starts a reactivation review", async () => {
    const s = await seller();
    const suspender = await staffClient(h, "SUPER_ADMIN");
    await suspender.c.post(`/api/v1/admin/sellers/${s.tamagneId}/suspend`, { reason: "Suspected impersonation" });
    const a = await s.c.post("/api/v1/appeals", { subjectType: "ACCOUNT_SUSPENSION", reason: "This is my own brand; I can show the trademark certificate." });
    expect(a.status).toBe(201);
    expect((await s.c.post("/api/v1/appeals", { subjectType: "ACCOUNT_SUSPENSION", reason: "Second appeal about the same thing" })).body.error.code).toBe("appeal_open");
    const appealId = a.body.data[0].id;

    const conflict = await suspender.c.post(`/api/v1/admin/appeals/${appealId}/decision`, { outcome: "OVERTURNED", note: "Looks fine to me" });
    expect(conflict.body.error.code).toBe("conflict_of_interest");
    const other = await staffClient(h, "SUPPORT_OFFICER");
    const d = await other.c.post(`/api/v1/admin/appeals/${appealId}/decision`, { outcome: "OVERTURNED", note: "Trademark certificate verified" });
    expect(d.body.data).toMatchObject({ outcome: "OVERTURNED", effect: "reactivation_pending_second_review" });
    expect((await lookup(s.tamagneId)).body.data.accountStatus).toBe("SUSPENDED"); // not yet: needs a second reviewer
    const mine = (await s.c.get("/api/v1/appeals/me")).body.data[0];
    expect(mine).toMatchObject({ status: "OVERTURNED", decisionNote: "Trademark certificate verified" });
    expect((await s.c.post("/api/v1/appeals", { subjectType: "ACCOUNT_SUSPENSION", reason: "x" })).status).toBe(422);
  });

  it("appeals about rejected documents re-open the business when overturned", async () => {
    const s = await seller();
    const biz = await s.c.post("/api/v1/businesses", { legalName: "Fixture Appeal PLC", legalForm: "OTHER" });
    await h.db.business.update({ where: { id: biz.body.data.id }, data: { status: "REJECTED", submittedAt: new Date(), decidedAt: new Date(), decisionReason: "Licence unreadable" } });
    const a = await s.c.post("/api/v1/appeals", { subjectType: "DOCUMENT_REJECTION", reason: "The licence is readable; please look at page two." });
    const staff = await staffClient(h, "VERIFICATION_OFFICER");
    const d = await staff.c.post(`/api/v1/admin/appeals/${a.body.data[0].id}/decision`, { outcome: "OVERTURNED", note: "Page two is clear" });
    expect(d.body.data.effect).toBe("business_reopened");
    expect((await s.c.get("/api/v1/businesses/me")).body.data.status).toBe("DRAFT");
  });

  it("revoking a badge is reasoned, audited and visible to buyers", async () => {
    const s = await seller();
    const badge = await h.db.sellerBadge.findFirstOrThrow({ where: { sellerProfileId: s.sellerId, level: "PHONE" } });
    const v = await staffClient(h, "VERIFICATION_OFFICER");
    const r = await v.c.post(`/api/v1/admin/badges/${badge.id}/revoke`, { reason: "Phone number reassigned by carrier" });
    expect(r.body.data.badges.find((b: { id: string }) => b.id === badge.id).status).toBe("REVOKED");
    expect((await lookup(s.tamagneId)).body.data.verification.phone.status).toBe("REVOKED");
    expect((await v.c.post(`/api/v1/admin/badges/${badge.id}/revoke`, { reason: "again again" })).body.error.code).toBe("already_closed");
  });
});

describe("seller search and detail", () => {
  it("finds sellers by Tamagne ID, phone or name; shows masked phone only", async () => {
    const s = await seller("Fixture Searchable Coffee");
    const v = await staffClient(h, "SUPPORT_OFFICER");
    for (const q of [s.tamagneId.toLowerCase(), s.phone, "searchable coffee"]) {
      const res = (await v.c.get(`/api/v1/admin/sellers?q=${encodeURIComponent(q)}`)).body.data;
      expect(res.map((r: { tamagneId: string }) => r.tamagneId), q).toContain(s.tamagneId);
    }
    const detail = (await v.c.get(`/api/v1/admin/sellers/${s.tamagneId}`)).body.data;
    expect(detail.user.phone).not.toBe(s.phone);
    expect(detail.user.phone).toMatch(/•/);
    expect(detail.badges[0]).toMatchObject({ level: "PHONE", status: "ACTIVE" });
    const finance = await staffClient(h, "FINANCE_OFFICER");
    expect((await finance.c.get(`/api/v1/admin/sellers/${s.tamagneId}`)).status).toBe(403);
  });
});

describe("expiry monitoring and job", () => {
  it("lists expiring badges; the job expires lapsed ones once and sends one reminder per badge", async () => {
    const lapsed = await seller();
    const soon = await seller();
    await h.db.$executeRaw`update seller_badges set "issuedAt" = now() - interval '400 days', "expiresAt" = now() - interval '1 day' where "sellerProfileId" = ${lapsed.sellerId}::uuid`;
    await h.db.$executeRaw`update seller_badges set "issuedAt" = now() - interval '350 days', "expiresAt" = now() + interval '10 days' where "sellerProfileId" = ${soon.sellerId}::uuid`;
    const v = await staffClient(h, "VERIFICATION_OFFICER");
    const list = (await v.c.get("/api/v1/admin/expiry?days=30")).body.data;
    expect(list.find((b: { seller: { tamagneId: string } }) => b.seller.tamagneId === lapsed.tamagneId)).toMatchObject({ lapsed: true });
    expect(list.find((b: { seller: { tamagneId: string } }) => b.seller.tamagneId === soon.tamagneId)).toMatchObject({ lapsed: false });

    const ops = h.app.get(OperationsService);
    const r1 = await ops.runExpiryJob();
    expect(r1.expiredBadges).toBeGreaterThanOrEqual(1);
    await ops.runExpiryJob();
    const badge = await h.db.sellerBadge.findFirstOrThrow({ where: { sellerProfileId: lapsed.sellerId } });
    expect(badge.status).toBe("EXPIRED");
    expect(await h.db.verificationEvent.count({ where: { badgeId: badge.id, type: "EXPIRED" } })).toBe(1);
    const reminders = await h.db.notification.count({ where: { userId: soon.userId, templateKey: "badge.expiring" } });
    expect(reminders).toBe(1);
    expect((await v.c.post("/api/v1/admin/jobs/expiry")).status).toBe(403); // Super Admin only
  });
});

describe("audit log viewer", () => {
  it("pages through entries and verifies the chain; restricted to Super Admin / Security", async () => {
    const sec = await staffClient(h, "SECURITY_OFFICER");
    const page = (await sec.c.get("/api/v1/admin/audit?action=auth.")).body.data;
    expect(page.entries.length).toBeGreaterThan(0);
    expect(page.entries.every((e: { action: string }) => e.action.startsWith("auth."))).toBe(true);
    const verify = (await sec.c.get("/api/v1/admin/audit/verify")).body.data;
    expect(verify).toMatchObject({ intact: true, firstBrokenSeq: null });
    const support = await staffClient(h, "SUPPORT_OFFICER");
    expect((await support.c.get("/api/v1/admin/audit")).status).toBe(403);
  });
});

describe("staff management", () => {
  it("Super Admins grant and revoke roles; no self-changes; never the last Super Admin", async () => {
    const admin = await staffClient(h, "SUPER_ADMIN");
    const target = new Client(h);
    const targetPhone = fakePhone();
    const { userId: targetId } = await target.signIn(targetPhone);
    expect((await admin.c.post("/api/v1/admin/staff", { phone: fakePhone(), role: "SUPPORT_OFFICER" })).body.error.code).toBe("user_not_found");
    const granted = (await admin.c.post("/api/v1/admin/staff", { phone: targetPhone, role: "SUPPORT_OFFICER" })).body.data;
    const grant = granted.find((g: { userId: string }) => g.userId === targetId);
    expect(grant).toMatchObject({ role: "SUPPORT_OFFICER", mfaEnrolled: false });
    expect((await admin.c.post("/api/v1/admin/staff", { phone: targetPhone, role: "SUPPORT_OFFICER" })).body.error.code).toBe("already_granted");

    const own = (await admin.c.get("/api/v1/admin/staff")).body.data.find((g: { userId: string }) => g.userId === admin.userId);
    expect((await admin.c.post(`/api/v1/admin/staff/${own.id}/revoke`)).body.error.code).toBe("self_grant");
    expect((await admin.c.post(`/api/v1/admin/staff/${grant.id}/revoke`)).status).toBe(200);
    expect(await h.db.session.count({ where: { userId: targetId, revokedAt: null } })).toBe(0);

    const v = await staffClient(h, "VERIFICATION_OFFICER");
    expect((await v.c.post("/api/v1/admin/staff", { phone: targetPhone, role: "SUPER_ADMIN" })).status).toBe(403);
  });
});

describe("pricing and revenue", () => {
  it("prices are admin-set and versioned; public plans show current prices; revenue comes from payments only", async () => {
    const fin = await staffClient(h, "FINANCE_OFFICER");
    const code = `premium_${Math.random().toString(36).slice(2, 8)}`;
    const plans = (await fin.c.post("/api/v1/admin/plans", { code, kind: "PREMIUM_SELLER", nameEn: "Premium seller", nameAm: "ፕሪሚየም ሻጭ" })).body.data;
    const plan = plans.find((p: { code: string }) => p.code === code);
    await fin.c.post(`/api/v1/admin/plans/${plan.id}/prices`, { amount: "199.00", interval: "MONTH" });
    const after = (await fin.c.post(`/api/v1/admin/plans/${plan.id}/prices`, { amount: "249.50", interval: "MONTH" })).body.data.find((p: { code: string }) => p.code === code);
    expect(after.currentPrices).toEqual([expect.objectContaining({ amount: "249.50", currency: "ETB", interval: "MONTH" })]);
    expect(after.priceHistory).toHaveLength(2);
    expect(after.priceHistory.find((p: { amount: string }) => p.amount === "199.00").validTo).not.toBeNull();
    expect((await fin.c.post(`/api/v1/admin/plans/${plan.id}/prices`, { amount: "12.345", interval: "MONTH" })).status).toBe(422);
    expect((await fin.c.post(`/api/v1/admin/plans/${plan.id}/prices`, { amount: "-1", interval: "MONTH" })).status).toBe(422);

    const pub = (await request(h.url).get("/api/v1/plans").expect(200)).body.data.find((p: { code: string }) => p.code === code);
    expect(pub.currentPrices[0].amount).toBe("249.50");
    expect(pub.priceHistory).toBeUndefined();

    const rev = (await fin.c.get("/api/v1/admin/revenue?months=12")).body.data;
    expect(rev.note).toMatch(/No payment provider/);
    const sec = await staffClient(h, "SECURITY_OFFICER");
    expect((await sec.c.get("/api/v1/admin/revenue")).status).toBe(403);
    expect((await sec.c.post(`/api/v1/admin/plans/${plan.id}/prices`, { amount: "1.00", interval: "MONTH" })).status).toBe(403);
  });
});

describe("overview and notifications", () => {
  it("overview counts queues; notifications are private to their user", async () => {
    const any = await staffClient(h, "FINANCE_OFFICER");
    const o = (await any.c.get("/api/v1/admin/overview")).body.data;
    expect(o.queues).toEqual(expect.objectContaining({ businesses: expect.any(Number), reports: expect.any(Number), appeals: expect.any(Number) }));
    const s = await seller();
    const v = await staffClient(h, "VERIFICATION_OFFICER");
    const badge = await h.db.sellerBadge.findFirstOrThrow({ where: { sellerProfileId: s.sellerId } });
    await v.c.post(`/api/v1/admin/badges/${badge.id}/revoke`, { reason: "Testing notifications" });
    const n = (await s.c.get("/api/v1/notifications")).body.data[0];
    expect(n).toMatchObject({ templateKey: "badge.revoked", read: false });
    const other = await seller();
    expect((await other.c.post(`/api/v1/notifications/${n.id}/read`)).body.data.updated).toBe(0);
    expect((await s.c.post(`/api/v1/notifications/${n.id}/read`)).body.data.updated).toBe(1);
  });
});

describe("MFA enrolment", () => {
  it("returns a server-rendered QR code for the authenticator app", async () => {
    const c = new Client(h);
    const phone = fakePhone();
    const { userId } = await c.signIn(phone);
    await h.db.staffRoleAssignment.create({ data: { userId, role: "SUPPORT_OFFICER" } });
    const e = (await c.post("/api/v1/auth/mfa/totp/enroll")).body.data;
    expect(e.qrSvg).toMatch(/^<svg/);
  });
});
