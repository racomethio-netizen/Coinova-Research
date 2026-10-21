import { ApiError } from "./api";

/**
 * In-memory fixed-window rate limiter.
 *
 * LIMITATION: state is per process. With multiple web instances each instance
 * enforces its own window, so the effective limit is (limit × instances).
 * Quotas that must be exact across instances (AI usage, alert notifications)
 * are enforced against the database instead. See docs/SECURITY.md.
 */

interface Bucket {
  count: number;
  resetAt: number;
}

const buckets = new Map<string, Bucket>();
const MAX_KEYS = 50_000;

export interface RateLimitResult {
  allowed: boolean;
  remaining: number;
  resetAt: number;
}

export function rateLimit(key: string, limit: number, windowMs: number, now = Date.now()): RateLimitResult {
  let b = buckets.get(key);
  if (!b || b.resetAt <= now) {
    if (buckets.size >= MAX_KEYS) sweep(now);
    b = { count: 0, resetAt: now + windowMs };
    buckets.set(key, b);
  }
  b.count += 1;
  return { allowed: b.count <= limit, remaining: Math.max(0, limit - b.count), resetAt: b.resetAt };
}

function sweep(now: number) {
  for (const [k, b] of buckets) if (b.resetAt <= now) buckets.delete(k);
  // If still full, drop the oldest entries to bound memory.
  if (buckets.size >= MAX_KEYS) {
    const excess = buckets.size - MAX_KEYS + 1000;
    let i = 0;
    for (const k of buckets.keys()) {
      if (i++ >= excess) break;
      buckets.delete(k);
    }
  }
}

export function enforceRateLimit(key: string, limit: number, windowMs: number) {
  const r = rateLimit(key, limit, windowMs);
  if (!r.allowed) {
    const retryAfter = Math.max(1, Math.ceil((r.resetAt - Date.now()) / 1000));
    throw new ApiError("rate_limited", "Too many requests. Please try again later.", undefined, {
      "Retry-After": String(retryAfter),
    });
  }
}

/** Test helper. */
export function resetRateLimits() {
  buckets.clear();
}
