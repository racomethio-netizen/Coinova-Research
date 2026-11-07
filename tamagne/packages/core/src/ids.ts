import { randomBytes } from "node:crypto";

/** Crockford base32 alphabet: no I, L, O, U (avoids misreading when typed from a card). */
const CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";
const TAMAGNE_ID_RE = /^TMG-[0-9A-HJKMNP-TV-Z]{10}$/;
const QR_TOKEN_RE = /^[A-Za-z0-9_-]{43}$/;

/**
 * Public seller identifier, e.g. `TMG-7K3QX9M2BD`. 50 random bits, non-sequential,
 * so IDs cannot be enumerated by counting. It is public (printed on profiles);
 * it is NOT an access credential.
 */
export function generateTamagneId(random: (n: number) => Buffer = randomBytes): string {
  const bytes = random(7); // 56 bits; we use the top 50.
  let value = 0n;
  for (const b of bytes) value = (value << 8n) | BigInt(b);
  value >>= 6n;
  let out = "";
  for (let i = 0; i < 10; i++) {
    out = CROCKFORD[Number(value & 31n)] + out;
    value >>= 5n;
  }
  return `TMG-${out}`;
}

/**
 * Normalizes user-typed input (case, spaces, Crockford look-alikes I/L→1, O→0).
 * Returns null if the result is not a well-formed Tamagne ID.
 */
export function normalizeTamagneId(input: string): string | null {
  const compact = input.trim().toUpperCase().replace(/[\s]/g, "");
  const body = compact.startsWith("TMG-") ? compact.slice(4) : compact.startsWith("TMG") ? compact.slice(3) : compact;
  const fixed = body.replace(/[IL]/g, "1").replace(/O/g, "0");
  const id = `TMG-${fixed}`;
  return TAMAGNE_ID_RE.test(id) ? id : null;
}

export function isTamagneId(value: string): boolean {
  return TAMAGNE_ID_RE.test(value);
}

/** 256-bit random, base64url (43 chars). Used in QR codes; revocable and rotatable. */
export function generateQrToken(): string {
  return randomBytes(32).toString("base64url");
}

export function isQrToken(value: string): boolean {
  return QR_TOKEN_RE.test(value);
}

/** Opaque bearer secret for sessions, API keys, OAuth state and nonce (store only its hash). */
export function generateOpaqueToken(bytes = 32): string {
  if (bytes < 16) throw new RangeError("opaque tokens need at least 128 bits");
  return randomBytes(bytes).toString("base64url");
}
