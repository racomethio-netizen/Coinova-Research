/**
 * Delivery retry policy (pure). Deliveries are attempted at most
 * MAX_DELIVERY_ATTEMPTS times with exponential backoff capped at 1 hour.
 */
export const MAX_DELIVERY_ATTEMPTS = 5;

export function nextAttemptDelayMs(attemptsSoFar: number): number {
  return Math.min(60 * 60_000, 60_000 * 2 ** Math.max(0, attemptsSoFar - 1));
}

export type DeliveryStatus = "pending" | "sending" | "sent" | "failed" | "skipped";

export interface DeliveryAttemptResult {
  status: DeliveryStatus;
  attempts: number;
  nextAttemptAt: string | null;
  lastError: string | null;
}

export function afterAttempt(
  previousAttempts: number,
  outcome: { ok: true } | { ok: false; error: string; permanent?: boolean },
  now: number,
): DeliveryAttemptResult {
  const attempts = previousAttempts + 1;
  if (outcome.ok) return { status: "sent", attempts, nextAttemptAt: null, lastError: null };
  const error = outcome.error.slice(0, 500);
  if (outcome.permanent || attempts >= MAX_DELIVERY_ATTEMPTS) {
    return { status: "failed", attempts, nextAttemptAt: null, lastError: error };
  }
  return { status: "pending", attempts, nextAttemptAt: new Date(now + nextAttemptDelayMs(attempts)).toISOString(), lastError: error };
}
