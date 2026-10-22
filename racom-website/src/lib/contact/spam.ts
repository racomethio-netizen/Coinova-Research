/** Minimum time a person plausibly needs to fill in the form. */
export const MIN_FILL_MS = 3_000;
/** Forms older than this are treated as stale (the page should be reloaded). */
export const MAX_FORM_AGE_MS = 24 * 60 * 60 * 1000;

export type SpamVerdict = { ok: true } | { ok: false; reason: "honeypot" | "too_fast" | "stale" };

export function checkSpam(
  { website, startedAt }: { website?: string; startedAt: number },
  now = Date.now(),
): SpamVerdict {
  if (website && website.trim() !== "") return { ok: false, reason: "honeypot" };
  const elapsed = now - startedAt;
  if (elapsed < MIN_FILL_MS) return { ok: false, reason: "too_fast" };
  if (elapsed > MAX_FORM_AGE_MS) return { ok: false, reason: "stale" };
  return { ok: true };
}
