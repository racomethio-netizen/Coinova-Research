import { createHmac, randomBytes, randomInt } from "node:crypto";
import { hmacSha256Hex, safeEqualHex } from "./crypto.js";

// ───────────── SMS one-time codes ─────────────

export const OTP_LENGTH = 6;
export const OTP_TTL_SECONDS = 300;
export const OTP_MAX_ATTEMPTS = 5;

/** Uniformly random 6-digit code from a CSPRNG. */
export function generateOtpCode(): string {
  return String(randomInt(0, 10 ** OTP_LENGTH)).padStart(OTP_LENGTH, "0");
}

/** Codes are bound to their challenge, so a code can't be replayed against another challenge. */
export function hashOtpCode(pepper: Buffer, challengeId: string, code: string): string {
  return hmacSha256Hex(pepper, `otp:${challengeId}:${code}`);
}

export function verifyOtpCode(pepper: Buffer, challengeId: string, code: string, storedHash: string): boolean {
  if (!/^\d{6}$/.test(code)) return false;
  return safeEqualHex(hashOtpCode(pepper, challengeId, code), storedHash);
}

// ───────────── TOTP (RFC 6238) for staff MFA ─────────────

const B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";

export function base32Encode(buf: Buffer): string {
  let bits = 0;
  let value = 0;
  let out = "";
  for (const byte of buf) {
    value = (value << 8) | byte;
    bits += 8;
    while (bits >= 5) {
      out += B32[(value >>> (bits - 5)) & 31];
      bits -= 5;
    }
  }
  if (bits > 0) out += B32[(value << (5 - bits)) & 31];
  return out;
}

export function base32Decode(input: string): Buffer {
  const clean = input.toUpperCase().replace(/=+$/, "").replace(/\s/g, "");
  let bits = 0;
  let value = 0;
  const out: number[] = [];
  for (const ch of clean) {
    const idx = B32.indexOf(ch);
    if (idx < 0) throw new Error("invalid base32");
    value = (value << 5) | idx;
    bits += 5;
    if (bits >= 8) {
      out.push((value >>> (bits - 8)) & 255);
      bits -= 8;
    }
  }
  return Buffer.from(out);
}

/** 160-bit secret, base32 (what authenticator apps expect). */
export function generateTotpSecret(): string {
  return base32Encode(randomBytes(20));
}

export const TOTP_PERIOD = 30;

export function totpStep(now: Date): number {
  return Math.floor(now.getTime() / 1000 / TOTP_PERIOD);
}

export function totpAt(secret: Buffer, step: number, digits = 6, algorithm: "sha1" | "sha256" | "sha512" = "sha1"): string {
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(step));
  const mac = createHmac(algorithm, secret).update(counter).digest();
  const offset = mac[mac.length - 1]! & 0x0f;
  const bin = (mac.readUInt32BE(offset) & 0x7fffffff) % 10 ** digits;
  return String(bin).padStart(digits, "0");
}

/**
 * Checks a 6-digit TOTP within ±`window` steps. Returns the matched step, or null.
 * Steps ≤ `lastUsedStep` are rejected so a code can't be replayed.
 */
export function verifyTotp(
  secretBase32: string,
  code: string,
  { now = new Date(), window = 1, lastUsedStep = null as number | null } = {},
): number | null {
  if (!/^\d{6}$/.test(code)) return null;
  const secret = base32Decode(secretBase32);
  const current = totpStep(now);
  let matched: number | null = null;
  for (let s = current - window; s <= current + window; s++) {
    // Check every step (no early exit) to keep timing independent of which step matches.
    const candidate = Buffer.from(totpAt(secret, s));
    if (candidate.length === code.length && safeEqualHex(candidate.toString("hex"), Buffer.from(code).toString("hex"))) {
      if (lastUsedStep === null || s > lastUsedStep) matched = s;
    }
  }
  return matched;
}

export function totpUri(secretBase32: string, accountLabel: string, issuer = "TAMAGNE"): string {
  const label = encodeURIComponent(`${issuer}:${accountLabel}`);
  const params = new URLSearchParams({ secret: secretBase32, issuer, algorithm: "SHA1", digits: "6", period: String(TOTP_PERIOD) });
  return `otpauth://totp/${label}?${params.toString()}`;
}
