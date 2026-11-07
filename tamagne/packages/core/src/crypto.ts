import { createCipheriv, createDecipheriv, createHash, createHmac, randomBytes, timingSafeEqual } from "node:crypto";

export function sha256Hex(input: string | Buffer): string {
  return createHash("sha256").update(input).digest("hex");
}

/** Keyed hash for values we must match but never store (identity subjects, OTP codes, IPs). */
export function hmacSha256Hex(key: Buffer, input: string | Buffer): string {
  if (key.length < 32) throw new RangeError("HMAC key must be at least 32 bytes");
  return createHmac("sha256", key).update(input).digest("hex");
}

/** Constant-time comparison of two equal-length hex digests. */
export function safeEqualHex(a: string, b: string): boolean {
  const ab = Buffer.from(a, "hex");
  const bb = Buffer.from(b, "hex");
  return ab.length === bb.length && ab.length > 0 && timingSafeEqual(ab, bb);
}

// ─────────────── Field encryption (AES-256-GCM with key rotation) ───────────────
//
// Layout: [version=1][keyId][iv(12)][tag(16)][ciphertext]
// `aad` binds a ciphertext to its column and row (e.g. "identity_verifications.verifiedNameEnc:<id>"),
// so a ciphertext copied into another row or column fails to decrypt.

const VERSION = 1;
const IV_LEN = 12;
const TAG_LEN = 16;
const HEADER_LEN = 2 + IV_LEN + TAG_LEN;

export interface FieldCipherOptions {
  /** keyId (0–255) → 32-byte key. Keep retired keys until data is re-encrypted. */
  keys: ReadonlyMap<number, Buffer>;
  activeKeyId: number;
}

export interface FieldCipher {
  encrypt(plaintext: string, aad: string): Buffer;
  decrypt(ciphertext: Buffer, aad: string): string;
  /** True when the value was written with a key other than the active one. */
  needsRotation(ciphertext: Buffer): boolean;
}

export function createFieldCipher({ keys, activeKeyId }: FieldCipherOptions): FieldCipher {
  for (const [id, key] of keys) {
    if (!Number.isInteger(id) || id < 0 || id > 255) throw new RangeError(`invalid key id ${id}`);
    if (key.length !== 32) throw new RangeError(`key ${id} must be 32 bytes`);
  }
  const active = keys.get(activeKeyId);
  if (!active) throw new RangeError(`active key ${activeKeyId} is not configured`);

  return {
    encrypt(plaintext, aad) {
      const iv = randomBytes(IV_LEN);
      const cipher = createCipheriv("aes-256-gcm", active, iv);
      cipher.setAAD(Buffer.from(aad, "utf8"));
      const ct = Buffer.concat([cipher.update(plaintext, "utf8"), cipher.final()]);
      return Buffer.concat([Buffer.from([VERSION, activeKeyId]), iv, cipher.getAuthTag(), ct]);
    },
    decrypt(blob, aad) {
      if (blob.length < HEADER_LEN || blob[0] !== VERSION) throw new Error("unsupported ciphertext");
      const key = keys.get(blob[1]!);
      if (!key) throw new Error("ciphertext key is not available");
      const iv = blob.subarray(2, 2 + IV_LEN);
      const tag = blob.subarray(2 + IV_LEN, HEADER_LEN);
      const decipher = createDecipheriv("aes-256-gcm", key, iv);
      decipher.setAAD(Buffer.from(aad, "utf8"));
      decipher.setAuthTag(tag);
      return Buffer.concat([decipher.update(blob.subarray(HEADER_LEN)), decipher.final()]).toString("utf8");
    },
    needsRotation(blob) {
      return blob[1] !== activeKeyId;
    },
  };
}

/**
 * Parses `FIELD_ENCRYPTION_KEYS` of the form "1:<base64 32 bytes>,2:<base64>".
 */
export function parseKeyRing(value: string): Map<number, Buffer> {
  const ring = new Map<number, Buffer>();
  for (const part of value.split(",").map((s) => s.trim()).filter(Boolean)) {
    const idx = part.indexOf(":");
    if (idx <= 0) throw new Error("key ring entries must look like <id>:<base64>");
    const id = Number(part.slice(0, idx));
    const key = Buffer.from(part.slice(idx + 1), "base64");
    if (!Number.isInteger(id) || id < 0 || id > 255) throw new Error("key ids must be integers 0–255");
    if (key.length !== 32) throw new Error(`key ${id} must decode to 32 bytes`);
    if (ring.has(id)) throw new Error(`duplicate key id ${id}`);
    ring.set(id, key);
  }
  if (ring.size === 0) throw new Error("key ring is empty");
  return ring;
}

// ─────────────── PKCE (RFC 7636) ───────────────

export function generatePkceVerifier(): string {
  // 32 random bytes → 43 base64url chars (within the 43–128 limit).
  return randomBytes(32).toString("base64url");
}

export function pkceChallengeS256(verifier: string): string {
  if (!/^[A-Za-z0-9\-._~]{43,128}$/.test(verifier)) throw new Error("invalid PKCE verifier");
  return createHash("sha256").update(verifier, "ascii").digest("base64url");
}
