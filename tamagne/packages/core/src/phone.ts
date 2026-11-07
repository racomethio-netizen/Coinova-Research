/**
 * Ethiopian mobile numbers. Accepts what people actually type:
 *   0911 22 33 44 · 911223344 · 251911223344 · +251 911-223-344 · 0711223344
 * and returns E.164 (+2519XXXXXXXX / +2517XXXXXXXX), or null.
 *
 * `allowTestRange` additionally accepts +999 numbers (unassigned country code),
 * used for synthetic test data. It must be false in production.
 */
const ET_MOBILE = /^\+251[79]\d{8}$/;
const TEST_RANGE = /^\+999\d{9}$/;

export interface NormalizePhoneOptions {
  allowTestRange?: boolean;
}

export function normalizePhone(input: string, { allowTestRange = false }: NormalizePhoneOptions = {}): string | null {
  if (typeof input !== "string" || input.length > 32) return null;
  const trimmed = input.trim();
  if (!/^\+?[\d\s\-().]+$/.test(trimmed)) return null;
  const plus = trimmed.startsWith("+");
  const digits = trimmed.replace(/\D/g, "");

  let e164: string;
  if (plus) e164 = `+${digits}`;
  else if (digits.startsWith("251")) e164 = `+${digits}`;
  else if (digits.length === 10 && digits.startsWith("0")) e164 = `+251${digits.slice(1)}`;
  else if (digits.length === 9) e164 = `+251${digits}`;
  else return null;

  if (ET_MOBILE.test(e164)) return e164;
  if (allowTestRange && TEST_RANGE.test(e164)) return e164;
  return null;
}

/** For display to the account holder only, e.g. "+251 9•• ••• 344". Never for public pages. */
export function maskPhone(e164: string): string {
  if (e164.length < 8) return "•••";
  return `${e164.slice(0, 4)} ${e164.slice(4, 5)}•• ••• ${e164.slice(-3)}`;
}
