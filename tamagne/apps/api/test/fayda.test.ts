import request from "supertest";
import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { Client, fakePhone, startApp, type TestHarness } from "./support.js";
import { startTestIdp, type TestIdp } from "./test-idp.js";

const REDIRECT = "http://localhost:3000/api/v1/fayda/callback";
let idp: TestIdp;
let h: TestHarness;

beforeAll(async () => {
  idp = await startTestIdp({ redirectUri: REDIRECT });
  h = await startApp({
    FAYDA_MODE: "sandbox",
    FAYDA_ISSUER: idp.issuer,
    FAYDA_CLIENT_ID: idp.clientId,
    FAYDA_REDIRECT_URI: REDIRECT,
    FAYDA_CLIENT_PRIVATE_KEY_JWK: JSON.stringify(idp.clientPrivateJwk),
    FAYDA_ACR_VALUES: "mosip:idp:acr:generated-code mosip:idp:acr:biometrics",
  });
});
afterAll(async () => {
  await h.close();
  await idp.close();
});
beforeEach(() => {
  idp.faults = {};
  idp.nextUser = { sub: `test-psut-${Math.random().toString(36).slice(2)}`, name: "Test Person (Fixture)" };
});

async function seller() {
  const c = new Client(h);
  const { userId } = await c.signIn(fakePhone());
  const s = (await c.post("/api/v1/sellers", { displayName: "Fixture Shop", sellerType: "INDIVIDUAL" })).body.data;
  await c.post("/api/v1/consents", { purpose: "PUBLIC_PROFILE_DISPLAY" });
  return { c, userId, sellerId: s.id as string, tamagneId: s.tamagneId as string };
}

/** Runs start → provider authorize → our callback. Returns the final redirect target. */
async function runFlow(c: Client, opts: { tamperState?: (s: string) => string; callbackClient?: Client } = {}) {
  const start = await c.post("/api/v1/fayda/start", { acceptConsent: true, locale: "en" });
  if (start.status !== 200) return { start, target: null as URL | null };
  const authUrl = new URL(start.body.data.authorizationUrl);
  const authz = await request(idp.issuer).get(authUrl.pathname + authUrl.search);
  const back = new URL(authz.headers.location!);
  if (opts.tamperState) back.searchParams.set("state", opts.tamperState(back.searchParams.get("state")!));
  const cb = await (opts.callbackClient ?? c).get(`/api/v1/fayda/callback${back.search}`);
  return { start, authUrl, callbackQuery: back.search, cb, target: cb.headers.location ? new URL(cb.headers.location) : null };
}

describe("Fayda eSignet relying party (against the TEST provider)", () => {
  it("authorization request uses PKCE S256, state, nonce and the configured client; no secrets leak", async () => {
    const s = await seller();
    const start = await s.c.post("/api/v1/fayda/start", { acceptConsent: true, locale: "am" });
    expect(start.status).toBe(200);
    const u = new URL(start.body.data.authorizationUrl);
    expect(u.origin).toBe(idp.issuer);
    const p = u.searchParams;
    expect(p.get("response_type")).toBe("code");
    expect(p.get("client_id")).toBe(idp.clientId);
    expect(p.get("redirect_uri")).toBe(REDIRECT);
    expect(p.get("code_challenge_method")).toBe("S256");
    expect(p.get("code_challenge")).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(p.get("state")).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(p.get("nonce")).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(p.get("ui_locales")).toBe("am");
    expect(JSON.parse(p.get("claims")!)).toEqual({ userinfo: { name: { essential: true } }, id_token: {} });
    expect(u.search).not.toMatch(/code_verifier|client_secret|assertion/);

    const row = await h.db.oidcAuthRequest.findFirstOrThrow({ where: { userId: s.userId } });
    expect(row.stateHash).not.toBe(p.get("state"));
    expect(Buffer.from(row.codeVerifierEnc).toString("latin1")).not.toMatch(/[A-Za-z0-9_-]{43}/); // verifier encrypted
    expect(row.expiresAt.getTime() - row.createdAt.getTime()).toBeLessThanOrEqual(10 * 60_000);
  });

  it("happy path: verified Fayda badge, only a subject hash stored, name encrypted, labelled as sandbox", async () => {
    const s = await seller();
    const { target } = await runFlow(s.c);
    expect(target!.pathname).toBe("/en/dashboard/identity");
    expect(target!.searchParams.get("fayda")).toBe("verified");
    expect(idp.stats.rejectedAssertions).toBe(0);

    const iv = await h.db.identityVerification.findFirstOrThrow({ where: { sellerProfileId: s.sellerId, status: "VERIFIED" } });
    expect(iv).toMatchObject({ environment: "SANDBOX", signatureVerified: true, issuer: idp.issuer, audience: idp.clientId, acr: "mosip:idp:acr:generated-code" });
    expect(iv.subjectHash).toMatch(/^[0-9a-f]{64}$/);
    const raw = JSON.stringify(await h.db.identityVerification.findMany({ where: { sellerProfileId: s.sellerId } }), (_k, v) => (v instanceof Uint8Array ? Buffer.from(v).toString("latin1") : v));
    expect(raw).not.toContain(idp.nextUser.sub);
    expect(raw).not.toContain("Test Person");
    const audit = JSON.stringify(await h.db.auditLog.findMany({ where: { actorUserId: s.userId } }), (_k, v) => (typeof v === "bigint" ? String(v) : v));
    expect(audit).not.toContain(idp.nextUser.sub);
    expect(audit).not.toContain("Test Person");

    const me = (await s.c.get("/api/v1/sellers/me")).body.data;
    expect(me.verification.faydaIdentity).toMatchObject({ status: "VERIFIED", testMode: true });
    expect(me.verification.phone.status).toBe("VERIFIED");
    const pub = await request(h.url).get(`/api/v1/verify/${s.tamagneId}`).expect(200);
    expect(pub.body.data.verification.faydaIdentity).toMatchObject({ status: "VERIFIED", testMode: true });
    expect(JSON.stringify(pub.body)).not.toContain("Test Person");

    // Already verified → cannot start again.
    expect((await s.c.post("/api/v1/fayda/start", { acceptConsent: true })).body.error.code).toBe("already_verified");
  });

  it("requires explicit consent and records it with the policy version", async () => {
    const s = await seller();
    const res = await s.c.post("/api/v1/fayda/start", {});
    expect(res.status).toBe(409);
    expect(res.body.error.code).toBe("consent_required");
    await s.c.post("/api/v1/fayda/start", { acceptConsent: true });
    const consent = await h.db.consentRecord.findFirstOrThrow({ where: { userId: s.userId, purpose: "FAYDA_IDENTITY_VERIFICATION" } });
    expect(consent).toMatchObject({ status: "GRANTED", policyVersion: "2026-11-draft" });
  });

  const failureCases: [string, Partial<TestIdp["faults"]>, string][] = [
    ["wrong issuer in the ID token", { idTokenIssuer: "https://evil.example" }, "id_token_wrong_issuer"],
    ["wrong audience", { idTokenAudience: "someone-else" }, "id_token_wrong_audience"],
    ["multiple audiences without our azp", { idTokenAudience: ["tamagne-test-client", "other"], idTokenAzp: "other" }, "id_token_wrong_audience"],
    ["expired ID token", { idTokenExpired: true }, "id_token_expired"],
    ["ID token issued in the future", { idTokenIatFuture: true }, "id_token_invalid"],
    ["signature by an unknown key", { signWithUnknownKey: true }, "id_token_invalid_signature"],
    ['"alg": "none"', { algNone: true }, "id_token_invalid_signature"],
    ["HS256 with the public key as secret", { hs256: true }, "id_token_invalid_signature"],
    ["nonce mismatch", { idTokenNonce: "attacker-chosen-nonce" }, "nonce_mismatch"],
    ["authentication method not allowed", { idTokenAcr: "mosip:idp:acr:password" }, "acr_not_allowed"],
    ["userinfo for another subject", { userinfoSub: "someone-else" }, "subject_mismatch"],
    ["unsigned userinfo", { userinfoUnsigned: true }, "userinfo_invalid"],
    ["userinfo for another client", { userinfoAudience: "other-client" }, "userinfo_invalid"],
    ["token endpoint error", { tokenError: true }, "token_exchange_failed"],
  ];

  it.each(failureCases)("rejects %s → %s, records the failure, issues no badge", async (_label, faults, expected) => {
    const s = await seller();
    idp.faults = faults;
    const { target } = await runFlow(s.c);
    expect(target!.searchParams.get("fayda")).toBe("failed");
    expect(target!.searchParams.get("reason")).toBe(expected);
    const iv = await h.db.identityVerification.findFirstOrThrow({ where: { sellerProfileId: s.sellerId } });
    expect(iv).toMatchObject({ status: "FAILED", failureCode: expected, signatureVerified: false, subjectHash: null });
    expect(await h.db.sellerBadge.count({ where: { sellerProfileId: s.sellerId, level: "FAYDA_IDENTITY" } })).toBe(0);
    expect((await s.c.get("/api/v1/sellers/me")).body.data.verification.faydaIdentity.status).toBe("NOT_VERIFIED");
  });

  it("rejects a discovery document for a different issuer (mix-up defence)", async () => {
    const local = await startTestIdp({ redirectUri: REDIRECT });
    local.faults = { discoveryIssuer: "https://other-idp.example" };
    const app = await startApp({
      FAYDA_MODE: "sandbox",
      FAYDA_ISSUER: local.issuer,
      FAYDA_CLIENT_ID: local.clientId,
      FAYDA_REDIRECT_URI: REDIRECT,
      FAYDA_CLIENT_PRIVATE_KEY_JWK: JSON.stringify(local.clientPrivateJwk),
    });
    try {
      const c = new Client(app);
      await c.signIn(fakePhone());
      await c.post("/api/v1/sellers", { displayName: "Fixture", sellerType: "INDIVIDUAL" });
      const res = await c.post("/api/v1/fayda/start", { acceptConsent: true });
      expect(res.status).toBe(503);
      expect(res.body.error.code).toBe("fayda_unreachable");
    } finally {
      await app.close();
      await local.close();
    }
  });

  it("state: unknown, tampered and replayed states are refused", async () => {
    const s = await seller();
    const tampered = await runFlow(s.c, { tamperState: (st) => `${st.slice(0, -2)}xx` });
    expect(tampered.target!.searchParams.get("reason")).toBe("state_invalid");
    expect(idp.stats.tokenRequests).toBeGreaterThan(0); // (from earlier tests) — no exchange happened for this one:
    const before = idp.stats.tokenRequests;

    const ok = await runFlow(s.c);
    expect(ok.target!.searchParams.get("fayda")).toBe("verified");
    const replay = await s.c.get(`/api/v1/fayda/callback${ok.callbackQuery}`);
    expect(new URL(replay.headers.location!).searchParams.get("reason")).toBe("state_invalid");
    expect(idp.stats.tokenRequests).toBe(before + 1); // the replay never reached the token endpoint
  });

  it("an expired authorization request is refused", async () => {
    const s = await seller();
    const start = await s.c.post("/api/v1/fayda/start", { acceptConsent: true });
    const authUrl = new URL(start.body.data.authorizationUrl);
    const back = new URL((await request(idp.issuer).get(authUrl.pathname + authUrl.search)).headers.location!);
    await h.db.$executeRaw`update oidc_auth_requests set "createdAt" = now() - interval '20 minutes', "expiresAt" = now() - interval '10 minutes' where "userId" = ${s.userId}::uuid`;
    const cb = await s.c.get(`/api/v1/fayda/callback${back.search}`);
    expect(new URL(cb.headers.location!).searchParams.get("reason")).toBe("state_expired");
  });

  it("a callback in another user's session is refused (no identity binding to the attacker)", async () => {
    const attacker = await seller();
    const victim = await seller();
    // The attacker starts the flow and lures the victim into completing it.
    const res = await runFlow(attacker.c, { callbackClient: victim.c });
    expect(res.target!.searchParams.get("reason")).toBe("state_invalid");
    expect(await h.db.identityVerification.count({ where: { sellerProfileId: { in: [attacker.sellerId, victim.sellerId] }, status: "VERIFIED" } })).toBe(0);
    // Without any session the browser is sent to sign in, and nothing is consumed.
    const anon = await request(h.url).get(`/api/v1/fayda/callback${res.callbackQuery}`);
    expect(anon.status).toBe(303);
    expect(anon.headers.location).toMatch(/\/en\/login\?fayda=session_missing$/);
  });

  it("the provider returning an error is recorded as provider_error", async () => {
    const s = await seller();
    const start = await s.c.post("/api/v1/fayda/start", { acceptConsent: true });
    const state = new URL(start.body.data.authorizationUrl).searchParams.get("state")!;
    const cb = await s.c.get(`/api/v1/fayda/callback?error=access_denied&state=${state}`);
    expect(new URL(cb.headers.location!).searchParams.get("reason")).toBe("provider_error");
  });

  it("one national identity cannot verify two seller accounts", async () => {
    const a = await seller();
    const b = await seller();
    const sub = `test-psut-shared-${Math.random().toString(36).slice(2)}`;
    idp.nextUser = { sub, name: "Test Person (Fixture)" };
    expect((await runFlow(a.c)).target!.searchParams.get("fayda")).toBe("verified");
    idp.nextUser = { sub, name: "Test Person (Fixture)" };
    const dup = await runFlow(b.c);
    expect(dup.target!.searchParams.get("reason")).toBe("duplicate_identity");
    expect(await h.db.auditLog.count({ where: { actorUserId: b.userId, action: "fayda.duplicate_identity" } })).toBe(1);
  });

  it("withdrawing Fayda consent revokes the badge and the identity result", async () => {
    const s = await seller();
    await runFlow(s.c);
    await s.c.delete("/api/v1/consents/FAYDA_IDENTITY_VERIFICATION");
    expect((await s.c.get("/api/v1/sellers/me")).body.data.verification.faydaIdentity.status).toBe("REVOKED");
    const iv = await h.db.identityVerification.findFirstOrThrow({ where: { sellerProfileId: s.sellerId, authRequestId: { not: null } }, orderBy: { createdAt: "desc" } });
    expect(iv.status).toBe("REVOKED");
  });

  it("consent withdrawn between start and callback stops the verification", async () => {
    const s = await seller();
    const start = await s.c.post("/api/v1/fayda/start", { acceptConsent: true });
    const authUrl = new URL(start.body.data.authorizationUrl);
    const back = new URL((await request(idp.issuer).get(authUrl.pathname + authUrl.search)).headers.location!);
    await s.c.delete("/api/v1/consents/FAYDA_IDENTITY_VERIFICATION");
    const cb = await s.c.get(`/api/v1/fayda/callback${back.search}`);
    expect(new URL(cb.headers.location!).searchParams.get("reason")).toBe("consent_withdrawn");
  });
});

describe("Fayda disabled (default)", () => {
  it("start is refused with a pointer to manual review; no provider is contacted", async () => {
    const app = await startApp();
    try {
      const c = new Client(app);
      await c.signIn(fakePhone());
      await c.post("/api/v1/sellers", { displayName: "Fixture", sellerType: "INDIVIDUAL" });
      const res = await c.post("/api/v1/fayda/start", { acceptConsent: true });
      expect(res.status).toBe(503);
      expect(res.body.error.code).toBe("fayda_unavailable");
      expect(res.body.error.message).toMatch(/manual identity review/);
    } finally {
      await app.close();
    }
  });
});
