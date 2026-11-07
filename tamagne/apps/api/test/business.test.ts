import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { randomInt } from "node:crypto";
import request from "supertest";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import type { MalwareScanner, ScanResult } from "../src/scanning/scanner.js";
import { EicarOnlyScanner } from "../src/scanning/scanner.js";
import { Client, fakePhone, staffClient, startApp, type TestHarness } from "./support.js";

/** Synthetic documents only. */
const pdf = (label: string) => Buffer.from(`%PDF-1.7\n% ${label} (synthetic fixture)\n1 0 obj << /Type /Catalog >> endobj\n%%EOF\n`);
const png = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 0x0d, 0x49, 0x48, 0x44, 0x52]);

let h: TestHarness & { storageDir: string };
beforeAll(async () => {
  h = await startApp();
});
afterAll(async () => {
  await h.close();
});

/** Random synthetic 10-digit TINs (the test database persists between runs). */
const freshTin = () => String(randomInt(1_000_000_000, 9_999_999_999));

async function sellerWithBusiness(opts: { consent?: boolean; tin?: string } = {}) {
  const c = new Client(h);
  const { userId } = await c.signIn(fakePhone());
  const s = (await c.post("/api/v1/sellers", { displayName: "Fixture Trading", sellerType: "REGISTERED_BUSINESS" })).body.data;
  await c.post("/api/v1/consents", { purpose: "PUBLIC_PROFILE_DISPLAY" });
  const b = await c.post("/api/v1/businesses", { legalName: "Fixture Trading PLC", legalForm: "PRIVATE_LIMITED_COMPANY", tinNumber: opts.tin ?? freshTin(), city: "Addis Ababa" });
  expect(b.status).toBe(201);
  if (opts.consent ?? true) await c.post("/api/v1/consents", { purpose: "BUSINESS_DOCUMENT_PROCESSING" });
  return { c, userId, tamagneId: s.tamagneId as string, sellerId: s.id as string, businessId: b.body.data.id as string };
}

const uploadLicence = (c: Client, label = "business licence") =>
  c.upload("/api/v1/businesses/documents", { type: "BUSINESS_LICENSE" }, { data: pdf(label), filename: "licence.pdf", contentType: "application/pdf" });

async function submitted() {
  const s = await sellerWithBusiness();
  const up = await uploadLicence(s.c);
  expect(up.status).toBe(201);
  expect((await s.c.post("/api/v1/businesses/me/submit")).status).toBe(200);
  const docId = up.body.data.documents[0].id as string;
  return { ...s, docId };
}

describe("seller: business details and uploads", () => {
  it("uploads are type-checked, scanned and stored encrypted; consent is required first", async () => {
    const s = await sellerWithBusiness({ consent: false });
    const noConsent = await uploadLicence(s.c);
    expect(noConsent.status).toBe(409);
    expect(noConsent.body.error.code).toBe("consent_required");

    await s.c.post("/api/v1/consents", { purpose: "BUSINESS_DOCUMENT_PROCESSING" });
    const ok = await uploadLicence(s.c, "Fixture Trading PLC licence");
    expect(ok.status).toBe(201);
    const doc = ok.body.data.documents[0];
    expect(doc).toMatchObject({ type: "BUSINESS_LICENSE", mimeType: "application/pdf", scanStatus: "CLEAN", reviewStatus: "PENDING" });
    expect(JSON.stringify(ok.body)).not.toMatch(/storageKey|sha256|licence\.pdf/);

    const row = await h.db.businessDocument.findUniqueOrThrow({ where: { id: doc.id } });
    expect(row.storageKey).toBe(`business-documents/${s.businessId}/${doc.id}`);
    const onDisk = readFileSync(join(h.storageDir, ...row.storageKey.split("/")));
    expect(onDisk.includes(Buffer.from("Fixture Trading PLC"))).toBe(false);
    expect(onDisk.includes(Buffer.from("%PDF"))).toBe(false);
    expect(statSync(join(h.storageDir, ...row.storageKey.split("/"))).mode & 0o777).toBe(0o600);
  });

  it("rejects disguised, active, oversized and infected files without storing them", async () => {
    const s = await sellerWithBusiness();
    const cases: [Buffer, string, number, string][] = [
      [Buffer.from("MZ\x90\x00 fake exe"), "application/pdf", 422, "document_unsupported_type"],
      [png, "application/pdf", 422, "document_type_mismatch"],
      [Buffer.from("%PDF-1.7\n<< /OpenAction << /S /JavaScript /JS (x) >> >>"), "application/pdf", 422, "document_active_content"],
      [Buffer.from(`%PDF-1.4\n${EicarOnlyScanner.EICAR}`), "application/pdf", 422, "document_malware_detected"],
    ];
    for (const [data, type, status, code] of cases) {
      const res = await s.c.upload("/api/v1/businesses/documents", { type: "BUSINESS_LICENSE" }, { data, filename: "x.pdf", contentType: type });
      expect(res.status, code).toBe(status);
      expect(res.body.error.code).toBe(code);
    }
    const big = await s.c.upload("/api/v1/businesses/documents", { type: "BUSINESS_LICENSE" }, { data: Buffer.concat([pdf("big"), Buffer.alloc(10 * 1024 * 1024)]), filename: "big.pdf", contentType: "application/pdf" });
    expect(big.status).toBe(413);
    expect((await s.c.upload("/api/v1/businesses/documents", { type: "BUSINESS_LICENSE" })).body.error.code).toBe("file_required");
    expect((await s.c.upload("/api/v1/businesses/documents", { type: "PASSPORT" }, { data: pdf("x"), filename: "x.pdf", contentType: "application/pdf" })).status).toBe(422);

    // Nothing reached storage; the infected upload is recorded (purged) and audited.
    expect(readdirSync(h.storageDir).includes("business-documents") ? readdirSync(join(h.storageDir, "business-documents")).includes(s.businessId) : false).toBe(false);
    const infected = await h.db.businessDocument.findFirstOrThrow({ where: { businessId: s.businessId, scanStatus: "INFECTED" } });
    expect(infected.purgedAt).not.toBeNull();
    const audit = await h.db.auditLog.findFirstOrThrow({ where: { action: "document.malware_detected", targetId: infected.id } });
    expect(audit.metadata).toMatchObject({ signature: "Eicar-Test-Signature" });
    expect((await s.c.get("/api/v1/businesses/me")).body.data.documents).toHaveLength(0);
  });

  it("submission needs the required documents; a submitted business is locked", async () => {
    const s = await sellerWithBusiness();
    await s.c.upload("/api/v1/businesses/documents", { type: "TIN_CERTIFICATE" }, { data: pdf("tin"), filename: "tin.pdf", contentType: "application/pdf" });
    const early = await s.c.post("/api/v1/businesses/me/submit");
    expect(early.status).toBe(409);
    expect(early.body.error.code).toBe("documents_missing");
    await uploadLicence(s.c);
    const sub = await s.c.post("/api/v1/businesses/me/submit");
    expect(sub.body.data.status).toBe("SUBMITTED");
    expect((await s.c.patch("/api/v1/businesses/me", { city: "Adama" })).body.error.code).toBe("business_locked");
    expect((await uploadLicence(s.c)).body.error.code).toBe("business_locked");
    expect((await s.c.post("/api/v1/businesses", { legalName: "Second", legalForm: "OTHER" })).status).toBe(409);
  });

  it("documents can be removed before submission; nobody else can touch them", async () => {
    const a = await sellerWithBusiness();
    const docId = (await uploadLicence(a.c)).body.data.documents[0].id;
    const b = await sellerWithBusiness();
    expect((await b.c.delete(`/api/v1/businesses/me/documents/${docId}`)).status).toBe(404);
    const removed = await a.c.delete(`/api/v1/businesses/me/documents/${docId}`);
    expect(removed.body.data.documents).toHaveLength(0);
    const row = await h.db.businessDocument.findUniqueOrThrow({ where: { id: docId } });
    expect(row.purgedAt).not.toBeNull();
    expect(() => statSync(join(h.storageDir, ...row.storageKey.split("/")))).toThrow();
  });

  it("validates business fields", async () => {
    const c = new Client(h);
    await c.signIn(fakePhone());
    await c.post("/api/v1/sellers", { displayName: "Fixture", sellerType: "INDIVIDUAL" });
    for (const bad of [{ legalName: "X", legalForm: "OTHER" }, { legalName: "Fixture PLC", legalForm: "OTHER", tinNumber: "12345" }, { legalName: "Fixture PLC", legalForm: "OTHER", status: "VERIFIED" }]) {
      expect((await c.post("/api/v1/businesses", bad)).status, JSON.stringify(bad)).toBe(422);
    }
  });
});

describe("reviewer workflow", () => {
  it("approval issues the Level 3 badge and shows on the public page", async () => {
    const s = await submitted();
    const officer = await staffClient(h, "VERIFICATION_OFFICER");

    const queue = await officer.c.get("/api/v1/admin/reviews/businesses").expect(200);
    expect(queue.body.data.businesses.map((b: { id: string }) => b.id)).toContain(s.businessId);

    const file = await officer.c.get(`/api/v1/admin/documents/${s.docId}/file`).expect(200);
    expect(file.headers["content-type"]).toBe("application/pdf");
    expect(file.headers["content-disposition"]).toMatch(/^attachment;/);
    expect(file.headers["content-security-policy"]).toContain("sandbox");
    expect(Buffer.from(file.body).toString()).toContain("business licence (synthetic fixture)");
    expect(await h.db.auditLog.count({ where: { action: "document.viewed", targetId: s.docId, actorUserId: officer.userId } })).toBe(1);

    const decision = await officer.c.post(`/api/v1/admin/businesses/${s.businessId}/decision`, {
      decision: "APPROVE",
      reason: "Licence matches the registered name and TIN",
      documents: [{ documentId: s.docId, status: "APPROVED" }],
    });
    expect(decision.status).toBe(200);
    expect(decision.body.data).toMatchObject({ status: "FINAL", outcome: "APPROVE" });

    const mine = await s.c.get("/api/v1/sellers/me");
    expect(mine.body.data.verification.business.status).toBe("VERIFIED");
    expect(mine.body.data.verification.faydaIdentity.status).toBe("NOT_VERIFIED"); // business ≠ identity
    expect((await s.c.get("/api/v1/businesses/me")).body.data.status).toBe("VERIFIED");
    const pub = await request(h.url).get(`/api/v1/verify/${s.tamagneId}`).expect(200);
    expect(pub.body.data.verification.business.status).toBe("VERIFIED");
    expect(JSON.stringify(pub.body)).not.toMatch(/Fixture Trading PLC|\d{10}/); // legal name and TIN are not public

    const docs = await h.db.businessDocument.findMany({ where: { businessId: s.businessId } });
    expect(docs.every((d) => d.reviewStatus === "APPROVED" && d.retainUntil !== null)).toBe(true);
    expect(await h.db.notification.count({ where: { userId: s.userId, templateKey: "business.verified" } })).toBe(1);
    expect(await h.db.verificationEvent.count({ where: { sellerProfileId: s.sellerId, level: "BUSINESS_DOCUMENTS", type: "ISSUED" } })).toBe(1);
  });

  it("rejection needs reasons, is shown to the seller, and the seller can fix and resubmit", async () => {
    const s = await submitted();
    const officer = await staffClient(h, "VERIFICATION_OFFICER");
    const noReason = await officer.c.post(`/api/v1/admin/businesses/${s.businessId}/decision`, { decision: "REJECT", reason: "Expired", documents: [{ documentId: s.docId, status: "REJECTED" }] });
    expect(noReason.body.error.code).toBe("reason_required");
    const rej = await officer.c.post(`/api/v1/admin/businesses/${s.businessId}/decision`, {
      decision: "REJECT",
      reason: "The licence expired in 2025",
      documents: [{ documentId: s.docId, status: "REJECTED", reason: "Expired licence" }],
    });
    expect(rej.body.data.outcome).toBe("REJECT");
    const view = (await s.c.get("/api/v1/businesses/me")).body.data;
    expect(view).toMatchObject({ status: "REJECTED", decisionReason: "The licence expired in 2025" });
    expect(view.documents[0]).toMatchObject({ reviewStatus: "REJECTED", rejectionReason: "Expired licence" });
    expect((await s.c.get("/api/v1/sellers/me")).body.data.verification.business.status).toBe("NOT_VERIFIED");

    // Fix: remove the rejected file, upload a new one, resubmit.
    await s.c.delete(`/api/v1/businesses/me/documents/${s.docId}`);
    await uploadLicence(s.c, "renewed licence");
    expect((await s.c.post("/api/v1/businesses/me/submit")).body.data.status).toBe("SUBMITTED");
  });

  it("change requests send the business back to draft with flagged documents", async () => {
    const s = await submitted();
    const officer = await staffClient(h, "VERIFICATION_OFFICER");
    await officer.c.post(`/api/v1/admin/businesses/${s.businessId}/decision`, {
      decision: "REQUEST_CHANGES",
      reason: "The scan is unreadable",
      documents: [{ documentId: s.docId, status: "NEEDS_RESUBMISSION", reason: "Blurry" }],
    });
    const view = (await s.c.get("/api/v1/businesses/me")).body.data;
    expect(view).toMatchObject({ status: "DRAFT", decisionReason: "The scan is unreadable" });
    expect(view.documents[0].reviewStatus).toBe("NEEDS_RESUBMISSION");
  });

  it("approval requires every document approved and the required types present", async () => {
    const s = await sellerWithBusiness();
    await uploadLicence(s.c);
    const extra = await s.c.upload("/api/v1/businesses/documents", { type: "TIN_CERTIFICATE" }, { data: pdf("tin"), filename: "t.pdf", contentType: "application/pdf" });
    await s.c.post("/api/v1/businesses/me/submit");
    const [licence, tin] = extra.body.data.documents as { id: string }[];
    const officer = await staffClient(h, "VERIFICATION_OFFICER");
    const partial = await officer.c.post(`/api/v1/admin/businesses/${s.businessId}/decision`, { decision: "APPROVE", reason: "ok looks fine", documents: [{ documentId: licence!.id, status: "APPROVED" }] });
    expect(partial.body.error.code).toBe("documents_undecided");
    const foreign = await officer.c.post(`/api/v1/admin/businesses/${s.businessId}/decision`, { decision: "APPROVE", reason: "ok looks fine", documents: [{ documentId: "00000000-0000-4000-8000-000000000000", status: "APPROVED" }] });
    expect(foreign.body.error.code).toBe("unknown_document");
    const ok = await officer.c.post(`/api/v1/admin/businesses/${s.businessId}/decision`, {
      decision: "APPROVE",
      reason: "ok looks fine",
      documents: [{ documentId: licence!.id, status: "APPROVED" }, { documentId: tin!.id, status: "APPROVED" }],
    });
    expect(ok.body.data.outcome).toBe("APPROVE");
  });

  it("high-risk decisions need a different second reviewer; both must approve", async () => {
    const s = await submitted();
    const first = await staffClient(h, "VERIFICATION_OFFICER");
    const pending = await first.c.post(`/api/v1/admin/businesses/${s.businessId}/decision`, {
      decision: "APPROVE",
      reason: "Override: registry lookup was down",
      riskLevel: "HIGH",
      documents: [{ documentId: s.docId, status: "APPROVED" }],
    });
    expect(pending.body.data).toMatchObject({ status: "PENDING_SECOND_REVIEW", outcome: null });
    expect((await s.c.get("/api/v1/sellers/me")).body.data.verification.business.status).toBe("NOT_VERIFIED");
    const reviewId = pending.body.data.reviewId;

    const same = await first.c.post(`/api/v1/admin/reviews/${reviewId}/second-review`, { decision: "APPROVE", reason: "agree" });
    expect(same.status).toBe(403);
    expect(same.body.error.code).toBe("same_reviewer");
    expect((await first.c.post(`/api/v1/admin/businesses/${s.businessId}/decision`, { decision: "REJECT", reason: "changed my mind", documents: [] })).body.error.code).toBe("awaiting_second_review");

    const second = await staffClient(h, "SECURITY_OFFICER");
    const done = await second.c.post(`/api/v1/admin/reviews/${reviewId}/second-review`, { decision: "APPROVE", reason: "Checked the registry manually" });
    expect(done.body.data).toMatchObject({ status: "FINAL", outcome: "APPROVE" });
    expect((await s.c.get("/api/v1/sellers/me")).body.data.verification.business.status).toBe("VERIFIED");
  });

  it("if the second reviewer disagrees, the business is not verified", async () => {
    const s = await submitted();
    const first = await staffClient(h, "VERIFICATION_OFFICER");
    const pending = await first.c.post(`/api/v1/admin/businesses/${s.businessId}/decision`, {
      decision: "APPROVE",
      reason: "Override requested",
      riskLevel: "HIGH",
      documents: [{ documentId: s.docId, status: "APPROVED" }],
    });
    const second = await staffClient(h, "SUPER_ADMIN");
    const done = await second.c.post(`/api/v1/admin/reviews/${pending.body.data.reviewId}/second-review`, { decision: "REJECT", reason: "Not enough evidence for an override" });
    expect(done.body.data.outcome).toBe("REJECT");
    const view = (await s.c.get("/api/v1/businesses/me")).body.data;
    expect(view).toMatchObject({ status: "REJECTED", decisionReason: "Not enough evidence for an override" });
    expect(await h.db.sellerBadge.count({ where: { sellerProfileId: s.sellerId, level: "BUSINESS_DOCUMENTS" } })).toBe(0);
  });

  it("a TIN can back only one verified business", async () => {
    const tin = freshTin();
    const officer = await staffClient(h, "VERIFICATION_OFFICER");
    const approve = (x: { businessId: string; docId: string }) =>
      officer.c.post(`/api/v1/admin/businesses/${x.businessId}/decision`, { decision: "APPROVE", reason: "documents valid", documents: [{ documentId: x.docId, status: "APPROVED" }] });
    const a = await sellerWithBusiness({ tin });
    const aDoc = (await uploadLicence(a.c)).body.data.documents[0].id;
    await a.c.post("/api/v1/businesses/me/submit");
    const b = await sellerWithBusiness({ tin });
    const bDoc = (await uploadLicence(b.c)).body.data.documents[0].id;
    await b.c.post("/api/v1/businesses/me/submit");
    expect((await approve({ businessId: a.businessId, docId: aDoc })).status).toBe(200);
    const dup = await approve({ businessId: b.businessId, docId: bDoc });
    expect(dup.status).toBe(409);
    expect(dup.body.error.code).toBe("duplicate_business");
    expect((await b.c.get("/api/v1/businesses/me")).body.data.status).toBe("SUBMITTED");
  });

  it("enforces roles, MFA and no self-review", async () => {
    const s = await submitted();
    const support = await staffClient(h, "SUPPORT_OFFICER");
    for (const r of [
      await support.c.get("/api/v1/admin/reviews/businesses"),
      await support.c.get(`/api/v1/admin/documents/${s.docId}/file`),
      await support.c.post(`/api/v1/admin/businesses/${s.businessId}/decision`, { decision: "APPROVE", reason: "nope", documents: [] }),
      await s.c.get(`/api/v1/admin/documents/${s.docId}/file`),
    ]) {
      expect(r.status).toBe(403);
    }
    // A verification officer who is also this seller cannot review their own business.
    await h.db.staffRoleAssignment.create({ data: { userId: s.userId, role: "VERIFICATION_OFFICER" } });
    const phone = (await h.db.user.findUniqueOrThrow({ where: { id: s.userId } })).phoneE164!;
    const own = new Client(h);
    await own.signIn(phone, "login");
    const { secret } = (await own.post("/api/v1/auth/mfa/totp/enroll")).body.data;
    const { base32Decode, totpAt, totpStep } = await import("@tamagne/core");
    await own.post("/api/v1/auth/mfa/totp/confirm", { code: totpAt(base32Decode(secret), totpStep(new Date())) });
    const self = await own.post(`/api/v1/admin/businesses/${s.businessId}/decision`, { decision: "APPROVE", reason: "my own", documents: [{ documentId: s.docId, status: "APPROVED" }] });
    expect(self.status).toBe(403);
    expect(self.body.error.code).toBe("own_account");
  });
});

describe("scanner outages", () => {
  it("documents with a failed scan cannot be approved or downloaded until a rescan passes", async () => {
    const scanner: MalwareScanner & { next: ScanResult["status"] } = {
      name: "switchable",
      next: "FAILED",
      async scan() {
        return { status: this.next, engine: "switchable" };
      },
    };
    const local = await startApp({}, { scanner });
    try {
      const c = new Client(local);
      await c.signIn(fakePhone());
      await c.post("/api/v1/sellers", { displayName: "Fixture", sellerType: "INDIVIDUAL" });
      await c.post("/api/v1/businesses", { legalName: "Fixture Outage PLC", legalForm: "OTHER" });
      await c.post("/api/v1/consents", { purpose: "BUSINESS_DOCUMENT_PROCESSING" });
      const up = await c.upload("/api/v1/businesses/documents", { type: "BUSINESS_LICENSE" }, { data: pdf("outage"), filename: "l.pdf", contentType: "application/pdf" });
      const doc = up.body.data.documents[0];
      expect(doc.scanStatus).toBe("FAILED");
      expect((await c.post("/api/v1/businesses/me/submit")).body.error.code).toBe("documents_missing");

      const officer = await staffClient(local, "VERIFICATION_OFFICER");
      expect((await officer.c.get(`/api/v1/admin/documents/${doc.id}/file`)).body.error.code).toBe("document_not_scanned");
      expect((await officer.c.post(`/api/v1/admin/documents/${doc.id}/rescan`)).body.data.scanStatus).toBe("FAILED");
      scanner.next = "CLEAN";
      expect((await officer.c.post(`/api/v1/admin/documents/${doc.id}/rescan`)).body.data.scanStatus).toBe("CLEAN");
      expect((await c.post("/api/v1/businesses/me/submit")).body.data.status).toBe("SUBMITTED");
    } finally {
      await local.close();
    }
  });

  it("with storage disabled, uploads are refused clearly", async () => {
    const local = await startApp({ STORAGE_DRIVER: "disabled" });
    try {
      const c = new Client(local);
      await c.signIn(fakePhone());
      await c.post("/api/v1/sellers", { displayName: "Fixture", sellerType: "INDIVIDUAL" });
      await c.post("/api/v1/businesses", { legalName: "Fixture PLC", legalForm: "OTHER" });
      await c.post("/api/v1/consents", { purpose: "BUSINESS_DOCUMENT_PROCESSING" });
      const res = await c.upload("/api/v1/businesses/documents", { type: "BUSINESS_LICENSE" }, { data: pdf("x"), filename: "l.pdf", contentType: "application/pdf" });
      expect(res.status).toBe(503);
      expect(res.body.error.code).toBe("storage_unavailable");
    } finally {
      await local.close();
    }
  });
});
