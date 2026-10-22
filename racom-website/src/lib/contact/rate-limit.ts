/**
 * Fixed-window in-memory rate limiter.
 *
 * Limitation: state is per server instance. On serverless or multi-instance
 * hosting each instance counts separately, so this is a first line of defence;
 * add a shared store (e.g. Redis/Upstash) or platform firewall rules for
 * stronger guarantees. See README → "Contact form".
 */
export type RateLimitResult = { allowed: boolean; remaining: number; retryAfterSeconds: number };

export function createRateLimiter({
  max,
  windowMs,
  maxKeys = 10_000,
}: {
  max: number;
  windowMs: number;
  maxKeys?: number;
}) {
  const hits = new Map<string, { count: number; resetAt: number }>();

  function sweep(now: number) {
    for (const [key, entry] of hits) if (entry.resetAt <= now) hits.delete(key);
  }

  return function check(key: string, now = Date.now()): RateLimitResult {
    let entry = hits.get(key);
    if (!entry || entry.resetAt <= now) {
      if (hits.size >= maxKeys) sweep(now);
      // If still full after sweeping, evict the oldest key to bound memory.
      if (hits.size >= maxKeys) {
        const oldest = hits.keys().next().value;
        if (oldest !== undefined) hits.delete(oldest);
      }
      entry = { count: 0, resetAt: now + windowMs };
      hits.set(key, entry);
    }
    entry.count += 1;
    const allowed = entry.count <= max;
    return {
      allowed,
      remaining: Math.max(0, max - entry.count),
      retryAfterSeconds: allowed ? 0 : Math.ceil((entry.resetAt - now) / 1000),
    };
  };
}
