import { base32Decode, totpAt, totpStep } from "@tamagne/core";
import request from "supertest";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { Client, fakePhone, startApp, type TestHarness } from "./support.js";

let h: TestHarness;
beforeAll(async () => {
  h = await startApp();
});
afterAll(async () => {
  await h.close();
});

describe("registration and phone OTP", () => {
  it("registers with phone + OTP, records consents and audit, starts a session", async () => {
    const c = new Client(h);
    const phone = fakePhone();
    const start = await c.post("/api/v1/auth/register", { phone, acceptTerms: true, acceptPrivacy: true, locale: "am" });
    expect(start.status).toBe(202);
    expect(start.body.data).toMatchObject({ challengeId: expect.any(String), resendAfterSeconds: 60 });
    const sms = h.sms.outbox.at(-1)!;
    expect(sms.to).toBe(phone);
    expect(sms.body).toMatch(/ታማኝ/); // Amharic message for locale "am"

    const code = h.sms.lastCodeFor(phone)!;
    const challenge = await h.db.phoneOtpChallenge.findUniqueOrThrow({ where: { id: start.body.data.challengeId } });
    expect(challenge.codeHash).not.toContain(code); // only an HMAC is stored

    const verify = await c.post("/api/v1/auth/otp/verify", { challengeId: start.body.data.challengeId, code, locale: "am" });
    expect(verify.status).toBe(200);
    expect(verify.body.data).toMatchObject({ isNew: true, hasSellerProfile: false });
    const cookie = verify.headers["set-cookie"] as unknown as string[];
    const session = cookie.find((x) => x.startsWith("tamagne_session="))!;
    expect(session).toMatch(/HttpOnly/);
    expect(session).toMatch(/SameSite=Lax/);

    const me = await c.get("/api/v1/auth/session").expect(200);
    expect(me.body.data).toMatchObject({ userId: verify.body.data.userId, locale: "am", roles: [], mfa: null });
    expect(me.body.data.phone).not.toBe(phone); // masked

    const consents = await h.db.consentRecord.findMany({ where: { userId: verify.body.data.userId } });
    expect(consents.map((x) => x.purpose).sort()).toEqual(["PRIVACY_POLICY", "TERMS_OF_SERVICE"]);
    expect(consents.every((x) => x.policyVersion && x.status === "GRANTED" && x.locale === "AM")).toBe(true);

    const audit = await h.db.auditLog.findMany({ where: { actorUserId: verify.body.data.userId } });
    expect(audit.map((a) => a.action)).toContain("auth.registered");
    expect(JSON.stringify(audit, (_k, v) => (typeof v === "bigint" ? v.toString() : v))).not.toContain(phone.slice(4));
  });

  it("requires accepting the terms and a valid Ethiopian number", async () => {
    const c = new Client(h);
    const noTerms = await c.post("/api/v1/auth/register", { phone: fakePhone(), acceptTerms: false, acceptPrivacy: true });
    expect(noTerms.status).toBe(422);
    expect(noTerms.body.error.details).toEqual(expect.arrayContaining([expect.objectContaining({ path: "acceptTerms" })]));
    const bad = await c.post("/api/v1/auth/register", { phone: "+1 202 555 0100", acceptTerms: true, acceptPrivacy: true });
    expect(bad.status).toBe(422);
    expect(bad.body.error.code).toBe("invalid_phone");
  });

  it("re-registering an existing number signs in without creating a duplicate user", async () => {
    const phone = fakePhone();
    const first = await new Client(h).signIn(phone);
    const second = await new Client(h).signIn(phone);
    expect(second).toMatchObject({ userId: first.userId, isNew: false });
    expect(await h.db.user.count({ where: { phoneE164: phone } })).toBe(1);
  });

  it("rejects wrong, reused, cross-challenge and expired codes; caps attempts", async () => {
    const c = new Client(h);
    const phone = fakePhone();
    const a = await c.post("/api/v1/auth/register", { phone, acceptTerms: true, acceptPrivacy: true });
    const code = h.sms.lastCodeFor(phone)!;
    const wrong = code === "000000" ? "111111" : "000000";

    const bad = await c.post("/api/v1/auth/otp/verify", { challengeId: a.body.data.challengeId, code: wrong });
    expect(bad.status).toBe(400);
    expect(bad.body.error.code).toBe("invalid_code");

    // A code from one challenge does not unlock another.
    h.clock.now += 61_000;
    const b = await c.post("/api/v1/auth/register", { phone, acceptTerms: true, acceptPrivacy: true });
    const codeB = h.sms.lastCodeFor(phone)!;
    if (codeB !== code) {
      expect((await c.post("/api/v1/auth/otp/verify", { challengeId: b.body.data.challengeId, code })).status).toBe(400);
    }

    // Exhaust the remaining attempts on A: even the right code then fails.
    for (let i = 0; i < 4; i++) await c.post("/api/v1/auth/otp/verify", { challengeId: a.body.data.challengeId, code: wrong });
    expect((await c.post("/api/v1/auth/otp/verify", { challengeId: a.body.data.challengeId, code })).status).toBe(400);
    expect((await h.db.phoneOtpChallenge.findUniqueOrThrow({ where: { id: a.body.data.challengeId } })).attempts).toBe(5);

    // B works once, then is spent.
    expect((await c.post("/api/v1/auth/otp/verify", { challengeId: b.body.data.challengeId, code: codeB })).status).toBe(200);
    expect((await new Client(h).post("/api/v1/auth/otp/verify", { challengeId: b.body.data.challengeId, code: codeB })).status).toBe(400);

    // Expired challenge.
    h.clock.now += 16 * 60_000;
    const e = await c.post("/api/v1/auth/login", { phone });
    const codeE = h.sms.lastCodeFor(phone)!;
    await h.db.$executeRaw`update phone_otp_challenges set "createdAt" = now() - interval '10 minutes', "expiresAt" = now() - interval '5 minutes' where id = ${e.body.data.challengeId}::uuid`;
    expect((await c.post("/api/v1/auth/otp/verify", { challengeId: e.body.data.challengeId, code: codeE })).status).toBe(400);
  });

  it("login does not reveal whether a number is registered and sends no SMS for unknown numbers", async () => {
    const c = new Client(h);
    const unknown = fakePhone();
    const before = h.sms.outbox.length;
    const res = await c.post("/api/v1/auth/login", { phone: unknown });
    expect(res.status).toBe(202);
    expect(Object.keys(res.body.data).sort()).toEqual(["challengeId", "expiresAt", "resendAfterSeconds"]);
    expect(h.sms.outbox.length).toBe(before);
    for (const code of ["000000", "123456"]) {
      expect((await c.post("/api/v1/auth/otp/verify", { challengeId: res.body.data.challengeId, code })).status).toBe(400);
    }
  });

  it("suspended users cannot sign in and their sessions stop working", async () => {
    const c = new Client(h);
    const phone = fakePhone();
    const { userId } = await c.signIn(phone);
    await h.db.user.update({ where: { id: userId }, data: { status: "SUSPENDED" } });
    expect((await c.get("/api/v1/auth/session")).body.data).toBeNull();
    const before = h.sms.outbox.length;
    expect((await new Client(h).post("/api/v1/auth/login", { phone })).status).toBe(202);
    expect(h.sms.outbox.length).toBe(before);
  });
});

describe("CSRF", () => {
  it("rejects state-changing requests without a matching token or from another origin", async () => {
    const server = h.url;
    const body = { phone: fakePhone(), acceptTerms: true, acceptPrivacy: true };
    const none = await request(server).post("/api/v1/auth/register").set("Origin", "http://localhost:3000").send(body);
    expect(none.status).toBe(403);
    expect(none.body.error.code).toBe("csrf_failed");

    const evil = new Client(h, "https://evil.example");
    expect((await evil.post("/api/v1/auth/register", body)).status).toBe(403);

    const c = new Client(h);
    const token = await c.token();
    const mismatch = await c.agent.post("/api/v1/auth/register").set("Origin", "http://localhost:3000").set("X-CSRF-Token", `${token.slice(0, -1)}x`).send(body);
    expect(mismatch.status).toBe(403);
    const noOrigin = await c.agent.post("/api/v1/auth/register").set("X-CSRF-Token", token).send(body);
    expect(noOrigin.status).toBe(403);
  });
});

describe("rate limiting", () => {
  it("limits code requests per phone (resend gap and 15-minute cap) with Retry-After", async () => {
    h.clock.now += 16 * 60_000;
    const c = new Client(h);
    const phone = fakePhone();
    const body = { phone, acceptTerms: true, acceptPrivacy: true };
    expect((await c.post("/api/v1/auth/register", body)).status).toBe(202);
    const tooSoon = await c.post("/api/v1/auth/register", body);
    expect(tooSoon.status).toBe(429);
    expect(tooSoon.body.error.code).toBe("rate_limited");
    expect(Number(tooSoon.headers["retry-after"])).toBeGreaterThan(0);

    h.clock.now += 61_000;
    expect((await c.post("/api/v1/auth/register", body)).status).toBe(202);
    h.clock.now += 61_000;
    expect((await c.post("/api/v1/auth/register", body)).status).toBe(202);
    h.clock.now += 61_000;
    expect((await c.post("/api/v1/auth/register", body)).status).toBe(429); // 4th within 15 minutes
  });

  it("limits code requests per IP across phones", async () => {
    const local = await startApp();
    try {
      const c = new Client(local);
      const results: number[] = [];
      for (let i = 0; i < 11; i++) {
        results.push((await c.post("/api/v1/auth/register", { phone: fakePhone(), acceptTerms: true, acceptPrivacy: true })).status);
      }
      expect(results.slice(0, 10).every((s) => s === 202)).toBe(true);
      expect(results[10]).toBe(429);
    } finally {
      await local.close();
    }
  });
});

describe("sellers", () => {
  it("requires a session", async () => {
    const c = new Client(h);
    expect((await c.get("/api/v1/sellers/me")).status).toBe(401);
    expect((await c.post("/api/v1/sellers", { displayName: "Fixture Shop", sellerType: "TELEGRAM_SHOP" })).status).toBe(401);
  });

  it("creates a profile with a Level 1 phone badge only; rejects duplicates and client-set fields", async () => {
    const c = new Client(h);
    await c.signIn(fakePhone());
    const created = await c.post("/api/v1/sellers", { displayName: "  Fixture   Shop ", displayNameAm: "የሙከራ ሱቅ", sellerType: "TELEGRAM_SHOP", city: "Addis Ababa" });
    expect(created.status).toBe(201);
    const s = created.body.data;
    expect(s.displayName).toBe("Fixture Shop");
    expect(s.tamagneId).toMatch(/^TMG-[0-9A-HJKMNP-TV-Z]{10}$/);
    expect(s.isPublic).toBe(false);
    expect(s.verification.phone.status).toBe("VERIFIED");
    expect(s.verification.faydaIdentity.status).toBe("NOT_VERIFIED");
    expect(s.verification.business.status).toBe("NOT_VERIFIED");
    expect(s.badges).toHaveLength(1);
    expect(s.badges[0]).toMatchObject({ level: "PHONE", method: "PHONE_OTP", status: "ACTIVE" });

    const dup = await c.post("/api/v1/sellers", { displayName: "Second Shop", sellerType: "INDIVIDUAL" });
    expect(dup.status).toBe(409);
    expect(dup.body.error.code).toBe("seller_exists");

    const events = await h.db.verificationEvent.findMany({ where: { sellerProfileId: s.id } });
    expect(events.map((e) => [e.level, e.type])).toEqual([["PHONE", "ISSUED"]]);
  });

  it("rejects attempts to set server-controlled fields or inject markup", async () => {
    const c = new Client(h);
    await c.signIn(fakePhone());
    for (const extra of [{ accountStatus: "ACTIVE" }, { tamagneId: "TMG-0000000000" }, { isPublic: true }, { userId: "x" }]) {
      const res = await c.post("/api/v1/sellers", { displayName: "Fixture Shop", sellerType: "INDIVIDUAL", ...extra });
      expect(res.status, JSON.stringify(extra)).toBe(422);
    }
    expect((await c.post("/api/v1/sellers", { displayName: "<script>x</script>", sellerType: "INDIVIDUAL" })).status).toBe(422);
    expect((await c.post("/api/v1/sellers", { displayName: "A", sellerType: "INDIVIDUAL" })).status).toBe(422);
  });

  it("updates profile details; a suspended seller cannot edit", async () => {
    const c = new Client(h);
    await c.signIn(fakePhone());
    const s = (await c.post("/api/v1/sellers", { displayName: "Fixture Shop", sellerType: "INDIVIDUAL" })).body.data;
    const upd = await c.patch("/api/v1/sellers/me", { city: "Hawassa", displayNameAm: "" });
    expect(upd.status).toBe(200);
    expect(upd.body.data).toMatchObject({ city: "Hawassa", displayNameAm: null, tamagneId: s.tamagneId });
    expect((await c.patch("/api/v1/sellers/me", { accountStatus: "ACTIVE" })).status).toBe(422);

    await h.db.sellerProfile.update({ where: { id: s.id }, data: { accountStatus: "SUSPENDED", suspendedAt: new Date(), suspensionReason: "test" } });
    const blocked = await c.patch("/api/v1/sellers/me", { city: "Adama" });
    expect(blocked.status).toBe(403);
    expect(blocked.body.error.code).toBe("seller_suspended");
    const view = await c.get("/api/v1/sellers/me").expect(200);
    expect(view.body.data.verification.phone.status).toBe("SUSPENDED");
  });

  it("one user cannot read or change another user's profile", async () => {
    const a = new Client(h);
    await a.signIn(fakePhone());
    const sa = (await a.post("/api/v1/sellers", { displayName: "Fixture A", sellerType: "INDIVIDUAL" })).body.data;
    const b = new Client(h);
    await b.signIn(fakePhone());
    expect((await b.get("/api/v1/sellers/me")).status).toBe(404);
    expect((await b.patch("/api/v1/sellers/me", { city: "X" })).status).toBe(404);
    expect((await h.db.sellerProfile.findUniqueOrThrow({ where: { id: sa.id } })).city).toBeNull();
  });
});

describe("consents", () => {
  it("granting and withdrawing public-profile consent toggles visibility and keeps history", async () => {
    const c = new Client(h);
    const { userId } = await c.signIn(fakePhone());
    await c.post("/api/v1/sellers", { displayName: "Fixture Shop", sellerType: "INDIVIDUAL" });
    const granted = await c.post("/api/v1/consents", { purpose: "PUBLIC_PROFILE_DISPLAY" });
    expect(granted.status).toBe(201);
    expect(granted.body.data.find((x: { purpose: string }) => x.purpose === "PUBLIC_PROFILE_DISPLAY").status).toBe("GRANTED");
    expect((await c.get("/api/v1/sellers/me")).body.data.isPublic).toBe(true);

    const withdrawn = await c.delete("/api/v1/consents/PUBLIC_PROFILE_DISPLAY");
    expect(withdrawn.status).toBe(200);
    expect((await c.get("/api/v1/sellers/me")).body.data.isPublic).toBe(false);
    const rows = await h.db.consentRecord.findMany({ where: { userId, purpose: "PUBLIC_PROFILE_DISPLAY" } });
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ status: "WITHDRAWN" });
    expect(rows[0]!.withdrawnAt).not.toBeNull();
  });

  it("required consents cannot be withdrawn piecemeal; unknown purposes are rejected", async () => {
    const c = new Client(h);
    await c.signIn(fakePhone());
    const res = await c.delete("/api/v1/consents/TERMS_OF_SERVICE");
    expect(res.status).toBe(422);
    expect(res.body.error.code).toBe("not_withdrawable");
    expect((await c.delete("/api/v1/consents/EVERYTHING")).status).toBe(422);
  });
});

describe("sessions", () => {
  it("logout revokes the session server-side; the old cookie stops working", async () => {
    const c = new Client(h);
    await c.signIn(fakePhone());
    const cookieHeader = (await c.get("/api/v1/auth/session")).request.cookies;
    expect((await c.post("/api/v1/auth/logout")).status).toBe(200);
    expect((await c.get("/api/v1/sellers/me")).status).toBe(401);
    // Replay the pre-logout cookie directly.
    const replay = await request(h.url).get("/api/v1/sellers/me").set("Cookie", cookieHeader);
    expect(replay.status).toBe(401);
  });

  it("idle sessions expire", async () => {
    const c = new Client(h);
    const { userId } = await c.signIn(fakePhone());
    expect((await c.get("/api/v1/auth/session")).body.data).not.toBeNull();
    await h.db.session.updateMany({ where: { userId }, data: { lastSeenAt: new Date(Date.now() - 8 * 86_400_000) } });
    expect((await c.get("/api/v1/auth/session")).body.data).toBeNull();
  });

  it("forged session cookies are ignored", async () => {
    const res = await request(h.url).get("/api/v1/sellers/me").set("Cookie", `tamagne_session=${"A".repeat(43)}`);
    expect(res.status).toBe(401);
  });
});

describe("staff roles and MFA", () => {
  async function staff(role: "SUPER_ADMIN" | "VERIFICATION_OFFICER" | "SECURITY_OFFICER") {
    const c = new Client(h);
    const phone = fakePhone();
    const { userId } = await c.signIn(phone);
    await h.db.staffRoleAssignment.create({ data: { userId, role } });
    // Sign in again so the session gets staff lifetime.
    await c.signIn(phone, "login");
    return { c, userId };
  }

  const codeFor = (secret: string, offsetSteps = 0) => totpAt(base32Decode(secret), totpStep(new Date()) + offsetSteps);

  it("non-staff are refused admin endpoints and MFA enrolment", async () => {
    const c = new Client(h);
    await c.signIn(fakePhone());
    const res = await c.get("/api/v1/admin/me");
    expect(res.status).toBe(403);
    expect(res.body.error.code).toBe("forbidden");
    expect((await c.post("/api/v1/auth/mfa/totp/enroll")).status).toBe(403);
    expect((await new Client(h).get("/api/v1/admin/me")).status).toBe(401);
  });

  it("staff must enrol and verify TOTP before admin access; codes cannot be replayed", async () => {
    const { c, userId } = await staff("VERIFICATION_OFFICER");
    const session = await h.db.session.findFirstOrThrow({ where: { userId, revokedAt: null }, orderBy: { createdAt: "desc" } });
    expect(session.expiresAt.getTime() - session.createdAt.getTime()).toBeLessThanOrEqual(12 * 3_600_000);

    const blocked = await c.get("/api/v1/admin/me");
    expect(blocked.status).toBe(403);
    expect(blocked.body.error.code).toBe("mfa_required");

    const enroll = await c.post("/api/v1/auth/mfa/totp/enroll");
    expect(enroll.status).toBe(201);
    const { secret, otpauthUri } = enroll.body.data;
    expect(otpauthUri).toMatch(/^otpauth:\/\/totp\//);
    const factor = await h.db.mfaFactor.findFirstOrThrow({ where: { userId, disabledAt: null } });
    expect(Buffer.from(factor.secretEnc).toString("latin1")).not.toContain(secret); // encrypted at rest

    expect((await c.post("/api/v1/auth/mfa/totp/confirm", { code: "000000" === codeFor(secret) ? "111111" : "000000" })).status).toBe(400);
    const code = codeFor(secret);
    expect((await c.post("/api/v1/auth/mfa/totp/confirm", { code })).status).toBe(200);
    expect((await c.get("/api/v1/admin/me")).body.data).toEqual({ userId, roles: ["VERIFICATION_OFFICER"] });

    // A fresh session must verify again; the already-used code is refused.
    const phone = (await h.db.user.findUniqueOrThrow({ where: { id: userId } })).phoneE164!;
    const c2 = new Client(h);
    await c2.signIn(phone, "login");
    expect((await c2.get("/api/v1/admin/me")).body.error.code).toBe("mfa_required");
    expect((await c2.post("/api/v1/auth/mfa/verify", { code })).status).toBe(400);
    expect((await c2.post("/api/v1/auth/mfa/verify", { code: codeFor(secret, 1) })).status).toBe(200);
    expect((await c2.get("/api/v1/admin/me")).status).toBe(200);

    // Second enrolment is refused.
    expect((await c2.post("/api/v1/auth/mfa/totp/enroll")).status).toBe(409);
    const audit = await h.db.auditLog.findMany({ where: { actorUserId: userId, action: { startsWith: "mfa." } } });
    expect(audit.map((a) => a.action)).toEqual(expect.arrayContaining(["mfa.enrollment_started", "mfa.failed", "mfa.enrolled", "mfa.verified"]));
  });

  it("role-restricted endpoints check the specific role", async () => {
    for (const [role, expected] of [["VERIFICATION_OFFICER", 403], ["SECURITY_OFFICER", 200], ["SUPER_ADMIN", 200]] as const) {
      const { c } = await staff(role);
      const { secret } = (await c.post("/api/v1/auth/mfa/totp/enroll")).body.data;
      await c.post("/api/v1/auth/mfa/totp/confirm", { code: codeFor(secret) });
      expect((await c.get("/api/v1/admin/audit/verify")).status, role).toBe(expected);
    }
  });

  it("a revoked role takes effect immediately", async () => {
    const { c, userId } = await staff("SUPER_ADMIN");
    const { secret } = (await c.post("/api/v1/auth/mfa/totp/enroll")).body.data;
    await c.post("/api/v1/auth/mfa/totp/confirm", { code: codeFor(secret) });
    expect((await c.get("/api/v1/admin/me")).status).toBe(200);
    const admin = await new Client(h).signIn(fakePhone());
    await h.db.staffRoleAssignment.updateMany({ where: { userId, revokedAt: null }, data: { revokedAt: new Date(), revokedById: admin.userId } });
    expect((await c.get("/api/v1/admin/me")).status).toBe(403);
  });
});
