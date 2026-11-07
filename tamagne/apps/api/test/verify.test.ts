import { base32Decode, generateQrToken, totpAt, totpStep } from "@tamagne/core";
import request from "supertest";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { RULES } from "../src/security/rate-limit.js";
import { Client, fakePhone, startApp, type TestHarness } from "./support.js";

let h: TestHarness;
beforeAll(async () => {
  h = await startApp();
});
afterAll(async () => {
  await h.close();
});

/** Registers a seller; optionally makes the profile public. */
async function seller(opts: { public?: boolean; name?: string } = {}) {
  const c = new Client(h);
  const phone = fakePhone();
  const { userId } = await c.signIn(phone);
  const created = await c.post("/api/v1/sellers", { displayName: opts.name ?? "Fixture Shop", displayNameAm: "የሙከራ ሱቅ", sellerType: "TELEGRAM_SHOP", city: "Addis Ababa" });
  if (opts.public ?? true) await c.post("/api/v1/consents", { purpose: "PUBLIC_PROFILE_DISPLAY" });
  return { c, phone, userId, tamagneId: created.body.data.tamagneId as string, sellerId: created.body.data.id as string };
}

const lookup = (id: string) => request(h.url).get(`/api/v1/verify/${encodeURIComponent(id)}`);

describe("public verification lookup", () => {
  it("returns only approved public fields for a public seller", async () => {
    const s = await seller();
    const res = await lookup(s.tamagneId).expect(200);
    expect(res.headers["cache-control"]).toBe("no-store");
    expect(Object.keys(res.body.data).sort()).toEqual(["accountStatus", "checkedAt", "displayName", "displayNameAm", "matchedBy", "tamagneId", "verification"]);
    expect(Object.keys(res.body.data.verification).sort()).toEqual(["business", "enhancedReview", "faydaIdentity", "identityManualReview", "phone"]);
    expect(res.body.data).toMatchObject({ tamagneId: s.tamagneId, displayName: "Fixture Shop", accountStatus: "ACTIVE", matchedBy: "tamagne_id" });
    expect(res.body.data.verification.phone.status).toBe("VERIFIED");
    expect(res.body.data.verification.phone.expiresAt).toEqual(expect.any(String));
    expect(res.body.data.verification.faydaIdentity.status).toBe("NOT_VERIFIED");
    expect(res.body.meta.disclaimer).toMatch(/not a government institution/);

    const text = JSON.stringify(res.body);
    for (const secret of [s.phone, s.phone.slice(4), s.userId, s.sellerId, "Addis Ababa", "TELEGRAM_SHOP"]) expect(text).not.toContain(secret);
  });

  it("normalizes typed IDs (case, missing dash, look-alike letters)", async () => {
    const s = await seller();
    for (const typed of [s.tamagneId.toLowerCase(), s.tamagneId.replace("-", ""), ` ${s.tamagneId} `]) {
      expect((await lookup(typed)).status, typed).toBe(200);
    }
  });

  it("private profiles are indistinguishable from unknown IDs", async () => {
    const hidden = await seller({ public: false });
    const a = await lookup(hidden.tamagneId);
    const b = await lookup("TMG-0000000000");
    const c = await lookup("not-an-id");
    for (const r of [a, b, c]) {
      expect(r.status).toBe(404);
      expect(r.body.error.code).toBe("not_found");
      expect(r.body.error.message).toBe(b.body.error.message);
    }
  });

  it("withdrawing public-profile consent hides the seller immediately", async () => {
    const s = await seller();
    expect((await lookup(s.tamagneId)).status).toBe(200);
    await s.c.delete("/api/v1/consents/PUBLIC_PROFILE_DISPLAY");
    expect((await lookup(s.tamagneId)).status).toBe(404);
  });

  it("a suspended seller shows as suspended, with no current verification", async () => {
    const s = await seller();
    await h.db.sellerProfile.update({ where: { id: s.sellerId }, data: { accountStatus: "SUSPENDED", suspendedAt: new Date(), suspensionReason: "fraud investigation (fixture)" } });
    const res = await lookup(s.tamagneId).expect(200);
    expect(res.body.data.accountStatus).toBe("SUSPENDED");
    expect(res.body.data.verification.phone).toEqual({ status: "SUSPENDED", verifiedAt: null, expiresAt: null });
    expect(JSON.stringify(res.body)).not.toContain("fraud investigation");
  });

  it("an expired phone badge is shown as expired even before the expiry job runs", async () => {
    const s = await seller();
    await h.db.$executeRaw`update seller_badges set "issuedAt" = now() - interval '400 days', "expiresAt" = now() - interval '35 days' where "sellerProfileId" = ${s.sellerId}::uuid`;
    const res = await lookup(s.tamagneId).expect(200);
    expect(res.body.data.verification.phone.status).toBe("EXPIRED");
  });

  it("is rate limited per IP", async () => {
    const local = await startApp();
    try {
      const { limit } = RULES.verifyPerIp;
      const statuses: number[] = [];
      for (let i = 0; i <= limit; i++) statuses.push((await request(local.url).get("/api/v1/verify/TMG-0000000000")).status);
      expect(statuses.slice(0, limit).every((s) => s === 404)).toBe(true);
      expect(statuses[limit]).toBe(429);
    } finally {
      await local.close();
    }
  });
});

describe("QR codes", () => {
  it("requires sign-in and a public profile; returns an SVG and a verify URL", async () => {
    expect((await request(h.url).get("/api/v1/sellers/me/qr")).status).toBe(401);
    const hidden = await seller({ public: false });
    const blocked = await hidden.c.get("/api/v1/sellers/me/qr");
    expect(blocked.status).toBe(409);
    expect(blocked.body.error.code).toBe("profile_not_public");

    const s = await seller();
    const res = await s.c.get("/api/v1/sellers/me/qr").expect(200);
    expect(res.headers["cache-control"]).toBe("no-store");
    expect(res.body.data.svg).toMatch(/^<svg[\s\S]*<\/svg>\s*$/);
    expect(res.body.data.svg).not.toMatch(/<script|on\w+=/i);
    const url = new URL(res.body.data.url);
    expect(url.origin).toBe("http://localhost:3000");
    const token = url.pathname.split("/").pop()!;
    expect(token).toMatch(/^[A-Za-z0-9_-]{43}$/);

    // Stable: the same active code is returned until rotated.
    expect((await s.c.get("/api/v1/sellers/me/qr")).body.data.url).toBe(res.body.data.url);
    // Own Tamagne ID works too.
    expect((await s.c.get(`/api/v1/sellers/${s.tamagneId}/qr`)).body.data.url).toBe(res.body.data.url);

    const viaQr = await lookup(token).expect(200);
    expect(viaQr.body.data).toMatchObject({ tamagneId: s.tamagneId, matchedBy: "qr_token" });
  });

  it("a seller cannot fetch another seller's QR code", async () => {
    const a = await seller();
    const b = await seller();
    const res = await b.c.get(`/api/v1/sellers/${a.tamagneId}/qr`);
    expect(res.status).toBe(404);
    expect(await h.db.qrToken.count({ where: { sellerProfileId: a.sellerId } })).toBe(0);
  });

  it("validates tokens strictly: malformed or unknown tokens find nothing", async () => {
    for (const t of [generateQrToken(), "short", `${generateQrToken()}x`, "../../etc/passwd", "%00"]) {
      expect((await lookup(t)).status, t).toBe(404);
    }
  });

  it("rotation revokes the old code (410 with a clear message) and the new one works", async () => {
    const s = await seller();
    const first = new URL((await s.c.get("/api/v1/sellers/me/qr")).body.data.url).pathname.split("/").pop()!;
    const rotated = await s.c.post("/api/v1/sellers/me/qr/rotate");
    expect(rotated.status).toBe(200);
    const second = new URL(rotated.body.data.url).pathname.split("/").pop()!;
    expect(second).not.toBe(first);

    const old = await lookup(first);
    expect(old.status).toBe(410);
    expect(old.body.error.code).toBe("qr_revoked");
    expect((await lookup(second)).status).toBe(200);
    expect(await h.db.qrToken.count({ where: { sellerProfileId: s.sellerId, status: "ACTIVE" } })).toBe(1);
    const audit = await h.db.auditLog.findMany({ where: { actorUserId: s.userId, action: { startsWith: "qr." } } });
    expect(audit.map((a) => a.action).sort()).toEqual(["qr.issued", "qr.rotated"]);
  });

  it("staff can revoke a seller's QR code only with the right role, MFA and a reason", async () => {
    const s = await seller();
    const token = new URL((await s.c.get("/api/v1/sellers/me/qr")).body.data.url).pathname.split("/").pop()!;

    async function staff(role: "SUPPORT_OFFICER" | "SECURITY_OFFICER") {
      const c = new Client(h);
      const phone = fakePhone();
      const { userId } = await c.signIn(phone);
      await h.db.staffRoleAssignment.create({ data: { userId, role } });
      await c.signIn(phone, "login");
      const { secret } = (await c.post("/api/v1/auth/mfa/totp/enroll")).body.data;
      await c.post("/api/v1/auth/mfa/totp/confirm", { code: totpAt(base32Decode(secret), totpStep(new Date())) });
      return c;
    }

    // A seller cannot use the staff endpoint.
    expect((await s.c.post(`/api/v1/admin/sellers/${s.tamagneId}/qr/revoke`, { reason: "trying it" })).status).toBe(403);
    const support = await staff("SUPPORT_OFFICER");
    expect((await support.post(`/api/v1/admin/sellers/${s.tamagneId}/qr/revoke`, { reason: "impersonation report" })).status).toBe(403);

    const security = await staff("SECURITY_OFFICER");
    expect((await security.post(`/api/v1/admin/sellers/${s.tamagneId}/qr/revoke`, { reason: "" })).status).toBe(422);
    const ok = await security.post(`/api/v1/admin/sellers/${s.tamagneId}/qr/revoke`, { reason: "impersonation report (fixture)" });
    expect(ok.status).toBe(200);
    expect(ok.body.data).toEqual({ revoked: 1 });
    expect((await lookup(token)).status).toBe(410);
    // The Tamagne ID itself still resolves; only the printed code is dead.
    expect((await lookup(s.tamagneId)).status).toBe(200);
  });

  it("revoked QR tokens can never be reactivated (database guard)", async () => {
    const s = await seller();
    await s.c.get("/api/v1/sellers/me/qr");
    await s.c.post("/api/v1/sellers/me/qr/rotate");
    const revoked = await h.db.qrToken.findFirstOrThrow({ where: { sellerProfileId: s.sellerId, status: "REVOKED" } });
    await expect(h.db.qrToken.update({ where: { id: revoked.id }, data: { status: "ACTIVE", revokedAt: null } })).rejects.toThrow(/cannot be reactivated/);
  });
});
