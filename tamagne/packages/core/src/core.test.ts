import { describe, expect, it } from "vitest";
import {
  buildPublicVerificationSummary,
  createFieldCipher,
  generateOpaqueToken,
  generateQrToken,
  generateTamagneId,
  hmacSha256Hex,
  isQrToken,
  isTamagneId,
  normalizeTamagneId,
  parseKeyRing,
  pkceChallengeS256,
  safeEqualHex,
  type BadgeInput,
} from "./index.js";

describe("identifiers", () => {
  it("generates well-formed, distinct Tamagne IDs", () => {
    const ids = new Set(Array.from({ length: 2000 }, () => generateTamagneId()));
    expect(ids.size).toBe(2000);
    for (const id of ids) expect(isTamagneId(id)).toBe(true);
  });

  it("encodes the top 50 bits of randomness deterministically", () => {
    expect(generateTamagneId(() => Buffer.alloc(7, 0))).toBe("TMG-0000000000");
    expect(generateTamagneId(() => Buffer.alloc(7, 0xff))).toBe("TMG-ZZZZZZZZZZ");
  });

  it("normalizes typed IDs and rejects malformed ones", () => {
    expect(normalizeTamagneId(" tmg-7k3qx9m2bd ")).toBe("TMG-7K3QX9M2BD");
    expect(normalizeTamagneId("TMG7K3QX9M2BD")).toBe("TMG-7K3QX9M2BD");
    expect(normalizeTamagneId("TMG-OOIILL0000")).toBe("TMG-0011110000");
    expect(normalizeTamagneId("TMG-7K3QX9M2B")).toBeNull();
    expect(normalizeTamagneId("TMG-7K3QX9M2BU")).toBeNull(); // U is not Crockford
    expect(normalizeTamagneId("'; drop table users; --")).toBeNull();
  });

  it("QR tokens are 256-bit base64url and validated strictly", () => {
    const t = generateQrToken();
    expect(t).toHaveLength(43);
    expect(isQrToken(t)).toBe(true);
    expect(isQrToken(t.slice(1))).toBe(false);
    expect(isQrToken(`${t.slice(1)}=`)).toBe(false);
    expect(() => generateOpaqueToken(8)).toThrow();
  });
});

describe("crypto", () => {
  const key = Buffer.alloc(32, 7);

  it("HMAC requires a strong key and compares in constant time", () => {
    expect(() => hmacSha256Hex(Buffer.alloc(8), "x")).toThrow();
    const a = hmacSha256Hex(key, "subject-1");
    expect(a).toMatch(/^[0-9a-f]{64}$/);
    expect(safeEqualHex(a, hmacSha256Hex(key, "subject-1"))).toBe(true);
    expect(safeEqualHex(a, hmacSha256Hex(key, "subject-2"))).toBe(false);
    expect(safeEqualHex(a, a.slice(2))).toBe(false);
  });

  it("field cipher round-trips and binds ciphertext to its AAD", () => {
    const cipher = createFieldCipher({ keys: new Map([[1, key]]), activeKeyId: 1 });
    const blob = cipher.encrypt("Test Person ሰላም", "identity_verifications.verifiedNameEnc:row-1");
    expect(blob.includes(Buffer.from("Test Person"))).toBe(false);
    expect(cipher.decrypt(blob, "identity_verifications.verifiedNameEnc:row-1")).toBe("Test Person ሰላም");
    expect(() => cipher.decrypt(blob, "identity_verifications.verifiedNameEnc:row-2")).toThrow();
    const tampered = Buffer.from(blob);
    tampered[tampered.length - 1]! ^= 1;
    expect(() => cipher.decrypt(tampered, "identity_verifications.verifiedNameEnc:row-1")).toThrow();
  });

  it("supports key rotation", () => {
    const k2 = Buffer.alloc(32, 9);
    const old = createFieldCipher({ keys: new Map([[1, key]]), activeKeyId: 1 });
    const blob = old.encrypt("v", "aad");
    const rotated = createFieldCipher({ keys: new Map([[1, key], [2, k2]]), activeKeyId: 2 });
    expect(rotated.needsRotation(blob)).toBe(true);
    expect(rotated.decrypt(blob, "aad")).toBe("v");
    expect(rotated.needsRotation(rotated.encrypt("v", "aad"))).toBe(false);
  });

  it("parses key rings and rejects weak or malformed keys", () => {
    const b64 = Buffer.alloc(32, 1).toString("base64");
    expect(parseKeyRing(`1:${b64}, 2:${b64}`).size).toBe(2);
    expect(() => parseKeyRing("")).toThrow();
    expect(() => parseKeyRing(`1:${Buffer.alloc(16).toString("base64")}`)).toThrow();
    expect(() => parseKeyRing(`1:${b64},1:${b64}`)).toThrow();
    expect(() => parseKeyRing(`x:${b64}`)).toThrow();
  });

  it("computes the RFC 7636 S256 challenge", () => {
    // Test vector from RFC 7636 Appendix B.
    expect(pkceChallengeS256("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")).toBe(
      "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
    );
    expect(() => pkceChallengeS256("short")).toThrow();
  });
});

describe("public verification summary", () => {
  const now = new Date("2026-11-07T12:00:00Z");
  const issued = new Date("2026-06-01T00:00:00Z");
  const future = new Date("2027-06-01T00:00:00Z");
  const badge = (b: Partial<BadgeInput> & Pick<BadgeInput, "level" | "method">): BadgeInput => ({
    status: "ACTIVE",
    issuedAt: issued,
    expiresAt: future,
    ...b,
  });

  it("phone verification is not identity verification", () => {
    const s = buildPublicVerificationSummary("ACTIVE", [badge({ level: "PHONE", method: "PHONE_OTP" })], { now, production: true });
    expect(s.phone.status).toBe("VERIFIED");
    expect(s.faydaIdentity.status).toBe("NOT_VERIFIED");
    expect(s.identityManualReview.status).toBe("NOT_VERIFIED");
  });

  it("a manual identity review is never reported as Fayda", () => {
    const s = buildPublicVerificationSummary(
      "ACTIVE",
      [badge({ level: "IDENTITY_MANUAL_REVIEW", method: "MANUAL_IDENTITY_REVIEW" })],
      { now, production: true },
    );
    expect(s.identityManualReview.status).toBe("VERIFIED");
    expect(s.faydaIdentity.status).toBe("NOT_VERIFIED");
  });

  it("a Fayda badge with the wrong method is ignored", () => {
    const s = buildPublicVerificationSummary(
      "ACTIVE",
      [badge({ level: "FAYDA_IDENTITY", method: "MANUAL_IDENTITY_REVIEW", identityEnvironment: "PRODUCTION" })],
      { now, production: true },
    );
    expect(s.faydaIdentity.status).toBe("NOT_VERIFIED");
  });

  it("sandbox Fayda results never count in production", () => {
    const b = [badge({ level: "FAYDA_IDENTITY", method: "FAYDA_ESIGNET_OIDC", identityEnvironment: "SANDBOX" })];
    expect(buildPublicVerificationSummary("ACTIVE", b, { now, production: true }).faydaIdentity.status).toBe("NOT_VERIFIED");
    const nonProd = buildPublicVerificationSummary("ACTIVE", b, { now, production: false }).faydaIdentity;
    expect(nonProd.status).toBe("VERIFIED");
    expect(nonProd.testMode).toBe(true); // always labelled as a sandbox test
    const prod = [badge({ level: "FAYDA_IDENTITY", method: "FAYDA_ESIGNET_OIDC", identityEnvironment: "PRODUCTION" })];
    const s = buildPublicVerificationSummary("ACTIVE", prod, { now, production: true });
    expect(s.faydaIdentity).toEqual({ status: "VERIFIED", verifiedAt: issued.toISOString(), expiresAt: future.toISOString() });
    expect(s.faydaIdentity.testMode).toBeUndefined();
  });

  it("expiry is evaluated at read time", () => {
    const s = buildPublicVerificationSummary(
      "ACTIVE",
      [badge({ level: "BUSINESS_DOCUMENTS", method: "MANUAL_DOCUMENT_REVIEW", expiresAt: new Date("2026-11-01T00:00:00Z") })],
      { now, production: true },
    );
    expect(s.business).toEqual({ status: "EXPIRED", verifiedAt: null, expiresAt: null });
  });

  it("revoked badges show as revoked; a newer active badge wins", () => {
    const revoked = badge({ level: "BUSINESS_DOCUMENTS", method: "MANUAL_DOCUMENT_REVIEW", status: "REVOKED" });
    expect(buildPublicVerificationSummary("ACTIVE", [revoked], { now, production: true }).business.status).toBe("REVOKED");
    const renewed = badge({ level: "BUSINESS_DOCUMENTS", method: "MANUAL_DOCUMENT_REVIEW" });
    expect(buildPublicVerificationSummary("ACTIVE", [revoked, renewed], { now, production: true }).business.status).toBe("VERIFIED");
  });

  it("a suspended account shows no current verification", () => {
    const s = buildPublicVerificationSummary(
      "SUSPENDED",
      [badge({ level: "PHONE", method: "PHONE_OTP" }), badge({ level: "FAYDA_IDENTITY", method: "FAYDA_ESIGNET_OIDC", identityEnvironment: "PRODUCTION" })],
      { now, production: true },
    );
    expect(s.phone.status).toBe("SUSPENDED");
    expect(s.faydaIdentity).toEqual({ status: "SUSPENDED", verifiedAt: null, expiresAt: null });
  });
});
