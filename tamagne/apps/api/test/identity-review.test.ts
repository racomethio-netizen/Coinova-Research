import { readdirSync } from "node:fs";
import { join } from "node:path";
import request from "supertest";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { EicarOnlyScanner } from "../src/scanning/scanner.js";
import { Client, fakePhone, staffClient, startApp, type TestHarness } from "./support.js";

/** Synthetic "ID photo": a PNG header plus filler; no real person. */
const idImage = Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), Buffer.from("synthetic ID fixture")]);

let h: TestHarness & { storageDir: string };
beforeAll(async () => {
  h = await startApp();
});
afterAll(async () => {
  await h.close();
});

async function seller(consent = true) {
  const c = new Client(h);
  const { userId } = await c.signIn(fakePhone());
  const s = (await c.post("/api/v1/sellers", { displayName: "Fixture Shop", sellerType: "INDIVIDUAL" })).body.data;
  await c.post("/api/v1/consents", { purpose: "PUBLIC_PROFILE_DISPLAY" });
  if (consent) await c.post("/api/v1/consents", { purpose: "IDENTITY_DOCUMENT_REVIEW" });
  return { c, userId, sellerId: s.id as string, tamagneId: s.tamagneId as string };
}

const submit = (c: Client, data = idImage, type = "image/png") =>
  c.upload("/api/v1/identity/manual", { documentType: "FAYDA_ID_CARD" }, { data, filename: "id.png", contentType: type });

describe("manual identity review (interim, never Fayda)", () => {
  it("needs consent; one open request at a time; infected files are not stored", async () => {
    const s = await seller(false);
    expect((await submit(s.c)).body.error.code).toBe("consent_required");
    await s.c.post("/api/v1/consents", { purpose: "IDENTITY_DOCUMENT_REVIEW" });
    const infected = await submit(s.c, Buffer.from(`%PDF-1.4\n${EicarOnlyScanner.EICAR}`), "application/pdf");
    expect(infected.body.error.code).toBe("document_malware_detected");
    expect(readdirSync(h.storageDir).includes("identity-documents") ? readdirSync(join(h.storageDir, "identity-documents")) : []).not.toContain(s.sellerId);

    const ok = await submit(s.c);
    expect(ok.status).toBe(201);
    expect(ok.body.data).toMatchObject({ documentType: "FAYDA_ID_CARD", status: "SUBMITTED", scanStatus: "CLEAN" });
    expect((await submit(s.c)).body.error.code).toBe("review_pending");
  });

  it("approval issues a separate 'identity reviewed manually' badge; Fayda stays NOT verified", async () => {
    const s = await seller();
    const req = (await submit(s.c)).body.data;
    const officer = await staffClient(h, "VERIFICATION_OFFICER");
    expect((await officer.c.get("/api/v1/admin/reviews/identity")).body.data.map((r: { id: string }) => r.id)).toContain(req.id);
    const file = await officer.c.get(`/api/v1/admin/identity-reviews/${req.id}/file`).expect(200);
    expect(file.headers["content-disposition"]).toMatch(/^attachment;/);
    expect(file.headers["content-security-policy"]).toContain("sandbox");
    expect(await h.db.auditLog.count({ where: { action: "identity_document.viewed", targetId: req.id } })).toBe(1);

    const d = await officer.c.post(`/api/v1/admin/identity-reviews/${req.id}/decision`, { decision: "APPROVE", reason: "Document genuine and matches the account holder" });
    expect(d.body.data).toMatchObject({ status: "FINAL", outcome: "APPROVE" });

    const me = (await s.c.get("/api/v1/sellers/me")).body.data;
    expect(me.verification.identityManualReview.status).toBe("VERIFIED");
    expect(me.verification.faydaIdentity.status).toBe("NOT_VERIFIED");
    const badge = await h.db.sellerBadge.findFirstOrThrow({ where: { sellerProfileId: s.sellerId, level: "IDENTITY_MANUAL_REVIEW" } });
    expect(badge.method).toBe("MANUAL_IDENTITY_REVIEW");
    expect(badge.expiresAt!.getTime() - badge.issuedAt!.getTime()).toBe(180 * 86_400_000);
    const pub = await request(h.url).get(`/api/v1/verify/${s.tamagneId}`).expect(200);
    expect(pub.body.data.verification.identityManualReview.status).toBe("VERIFIED");
    expect(pub.body.data.verification.faydaIdentity.status).toBe("NOT_VERIFIED");

    const row = await h.db.identityReviewRequest.findUniqueOrThrow({ where: { id: req.id } });
    expect(row.retainUntil!.getTime() - row.decidedAt!.getTime()).toBe(30 * 86_400_000);
    expect((await submit(s.c)).body.error.code).toBe("already_verified");
  });

  it("the database refuses to turn a manual review into a Fayda badge", async () => {
    const s = await seller();
    const req = (await submit(s.c)).body.data;
    const officer = await staffClient(h, "VERIFICATION_OFFICER");
    const d = await officer.c.post(`/api/v1/admin/identity-reviews/${req.id}/decision`, { decision: "APPROVE", reason: "Document genuine" });
    await expect(
      h.db.sellerBadge.create({
        data: { sellerProfileId: s.sellerId, level: "FAYDA_IDENTITY", method: "MANUAL_IDENTITY_REVIEW", reviewRecordId: d.body.data.reviewId, status: "ACTIVE", issuedAt: new Date(), expiresAt: new Date(Date.now() + 86_400_000) },
      }),
    ).rejects.toThrow();
  });

  it("rejection records the reason; the seller can submit again", async () => {
    const s = await seller();
    const req = (await submit(s.c)).body.data;
    const officer = await staffClient(h, "VERIFICATION_OFFICER");
    await officer.c.post(`/api/v1/admin/identity-reviews/${req.id}/decision`, { decision: "REJECT", reason: "Photo is unreadable" });
    expect((await s.c.get("/api/v1/identity/manual")).body.data).toMatchObject({ status: "REJECTED", decisionReason: "Photo is unreadable" });
    expect((await submit(s.c)).status).toBe(201);
  });

  it("high-risk decisions need a second reviewer; withdrawal deletes the image", async () => {
    const s = await seller();
    const req = (await submit(s.c)).body.data;
    const first = await staffClient(h, "VERIFICATION_OFFICER");
    const p = await first.c.post(`/api/v1/admin/identity-reviews/${req.id}/decision`, { decision: "APPROVE", reason: "Unusual document, approving", riskLevel: "HIGH" });
    expect(p.body.data.status).toBe("PENDING_SECOND_REVIEW");
    expect((await s.c.get("/api/v1/sellers/me")).body.data.verification.identityManualReview.status).toBe("NOT_VERIFIED");
    const second = await staffClient(h, "SUPER_ADMIN");
    const f = await second.c.post(`/api/v1/admin/reviews/${p.body.data.reviewId}/second-review`, { decision: "APPROVE", reason: "Confirmed" });
    expect(f.body.data).toMatchObject({ status: "FINAL", outcome: "APPROVE" });
    expect((await s.c.get("/api/v1/sellers/me")).body.data.verification.identityManualReview.status).toBe("VERIFIED");

    const w = await seller();
    const wr = (await submit(w.c)).body.data;
    const key = (await h.db.identityReviewRequest.findUniqueOrThrow({ where: { id: wr.id } })).storageKey;
    expect((await w.c.delete("/api/v1/identity/manual")).body.data.status).toBe("WITHDRAWN");
    expect(() => readdirSync(join(h.storageDir, ...key.split("/")))).toThrow();
  });

  it("roles: sellers and support officers cannot see or decide identity reviews; no self-review", async () => {
    const s = await seller();
    const req = (await submit(s.c)).body.data;
    const support = await staffClient(h, "SUPPORT_OFFICER");
    expect((await support.c.get("/api/v1/admin/reviews/identity")).status).toBe(403);
    expect((await support.c.get(`/api/v1/admin/identity-reviews/${req.id}/file`)).status).toBe(403);
    expect((await s.c.post(`/api/v1/admin/identity-reviews/${req.id}/decision`, { decision: "APPROVE", reason: "self" })).status).toBe(403);
  });

  it("decisions are final in the database", async () => {
    const s = await seller();
    const req = (await submit(s.c)).body.data;
    const officer = await staffClient(h, "VERIFICATION_OFFICER");
    await officer.c.post(`/api/v1/admin/identity-reviews/${req.id}/decision`, { decision: "REJECT", reason: "Expired ID" });
    await expect(h.db.identityReviewRequest.update({ where: { id: req.id }, data: { status: "APPROVED" } })).rejects.toThrow(/final/);
  });
});
