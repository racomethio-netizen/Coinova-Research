import { describe, expect, it } from "vitest";
import {
  base32Decode,
  base32Encode,
  generateOtpCode,
  generateTotpSecret,
  hashOtpCode,
  maskPhone,
  normalizePhone,
  totpAt,
  totpStep,
  totpUri,
  verifyOtpCode,
  verifyTotp,
} from "./index.js";

describe("normalizePhone", () => {
  it.each([
    ["0911223344", "+251911223344"],
    ["0911 22 33 44", "+251911223344"],
    ["911223344", "+251911223344"],
    ["251911223344", "+251911223344"],
    ["+251 911-223-344", "+251911223344"],
    ["(0711) 223344", "+251711223344"],
  ])("%s → %s", (input, expected) => {
    expect(normalizePhone(input)).toBe(expected);
  });

  it.each(["", "0111223344", "+251111223344", "+1 202 555 0100", "09112233", "abc", "0911223344; drop", "+25191122334455"])(
    "rejects %s",
    (input) => {
      expect(normalizePhone(input)).toBeNull();
    },
  );

  it("accepts the synthetic +999 range only when allowed", () => {
    expect(normalizePhone("+999000000001")).toBeNull();
    expect(normalizePhone("+999000000001", { allowTestRange: true })).toBe("+999000000001");
  });

  it("masks for display", () => {
    expect(maskPhone("+251911223344")).toBe("+251 9•• ••• 344");
  });
});

describe("SMS OTP", () => {
  const pepper = Buffer.alloc(32, 3);

  it("codes are 6 digits and well distributed", () => {
    const codes = Array.from({ length: 5000 }, generateOtpCode);
    for (const c of codes) expect(c).toMatch(/^\d{6}$/);
    expect(new Set(codes).size).toBeGreaterThan(4900);
  });

  it("hash is bound to the challenge", () => {
    const h = hashOtpCode(pepper, "c1", "123456");
    expect(verifyOtpCode(pepper, "c1", "123456", h)).toBe(true);
    expect(verifyOtpCode(pepper, "c2", "123456", h)).toBe(false);
    expect(verifyOtpCode(pepper, "c1", "123457", h)).toBe(false);
    expect(verifyOtpCode(pepper, "c1", "12345", h)).toBe(false);
    expect(verifyOtpCode(pepper, "c1", "abcdef", h)).toBe(false);
  });
});

describe("TOTP", () => {
  it("base32 round-trips (RFC 4648 vector)", () => {
    expect(base32Encode(Buffer.from("foobar"))).toBe("MZXW6YTBOI");
    expect(base32Decode("MZXW6YTBOI").toString()).toBe("foobar");
    expect(() => base32Decode("1!")).toThrow();
  });

  it("matches RFC 6238 appendix B (SHA-1, 8 digits)", () => {
    const secret = Buffer.from("12345678901234567890");
    const vectors: [number, string][] = [
      [59, "94287082"],
      [1111111109, "07081804"],
      [1111111111, "14050471"],
      [1234567890, "89005924"],
      [2000000000, "69279037"],
    ];
    for (const [t, code] of vectors) expect(totpAt(secret, Math.floor(t / 30), 8)).toBe(code);
  });

  it("verifies within the window and rejects replay", () => {
    const secret = generateTotpSecret();
    const now = new Date("2026-11-07T10:00:00Z");
    const step = totpStep(now);
    const code = totpAt(base32Decode(secret), step);
    expect(verifyTotp(secret, code, { now })).toBe(step);
    expect(verifyTotp(secret, code, { now: new Date(now.getTime() + 30_000) })).toBe(step);
    expect(verifyTotp(secret, code, { now: new Date(now.getTime() + 95_000) })).toBeNull();
    expect(verifyTotp(secret, code, { now, lastUsedStep: step })).toBeNull();
    expect(verifyTotp(secret, "000000x", { now })).toBeNull();
  });

  it("builds an otpauth URI", () => {
    expect(totpUri("ABC", "staff@tamagne")).toBe(
      "otpauth://totp/TAMAGNE%3Astaff%40tamagne?secret=ABC&issuer=TAMAGNE&algorithm=SHA1&digits=6&period=30",
    );
  });
});
