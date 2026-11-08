import { HttpStatus, Inject, Injectable, Logger, type OnModuleDestroy } from "@nestjs/common";
import { Redis } from "ioredis";
import { ApiError } from "../common/http.js";
import type { AppConfig } from "../config/env.js";

/** Fixed-window counter store. `hit` returns the count in the current window and its remaining TTL. */
export interface RateLimitStore {
  hit(key: string, windowSeconds: number): Promise<{ count: number; ttlSeconds: number }>;
  close(): Promise<void>;
}

export class MemoryRateLimitStore implements RateLimitStore {
  private readonly buckets = new Map<string, { count: number; resetAt: number }>();
  constructor(private readonly now: () => number = Date.now) {}

  async hit(key: string, windowSeconds: number) {
    const t = this.now();
    let b = this.buckets.get(key);
    if (!b || b.resetAt <= t) {
      b = { count: 0, resetAt: t + windowSeconds * 1000 };
      this.buckets.set(key, b);
    }
    b.count += 1;
    if (this.buckets.size > 100_000) this.sweep(t);
    return { count: b.count, ttlSeconds: Math.max(1, Math.ceil((b.resetAt - t) / 1000)) };
  }

  private sweep(t: number) {
    for (const [k, b] of this.buckets) if (b.resetAt <= t) this.buckets.delete(k);
  }

  async close() {}
}

/** Atomic INCR + EXPIRE NX in one round trip; shared across API instances. */
export class RedisRateLimitStore implements RateLimitStore {
  constructor(private readonly redis: Redis) {}

  async hit(key: string, windowSeconds: number) {
    const result = (await this.redis
      .multi()
      .incr(key)
      .expire(key, windowSeconds, "NX")
      .ttl(key)
      .exec()) as [Error | null, unknown][] | null;
    if (!result) throw new Error("rate limit transaction aborted");
    for (const [err] of result) if (err) throw err;
    const count = Number(result[0]![1]);
    const ttl = Number(result[2]![1]);
    return { count, ttlSeconds: ttl > 0 ? ttl : windowSeconds };
  }

  async close() {
    await this.redis.quit();
  }
}

export interface RateRule {
  /** Short stable name, part of the key. */
  name: string;
  limit: number;
  windowSeconds: number;
}

export const RULES = {
  otpSendPerPhone: { name: "otp-send-phone", limit: 3, windowSeconds: 900 },
  otpResendGap: { name: "otp-resend-gap", limit: 1, windowSeconds: 60 },
  /** Limit comes from OTP_SEND_LIMIT_PER_IP (see AuthService). */
  otpSendPerIp: { name: "otp-send-ip", limit: 30, windowSeconds: 900 },
  otpVerifyPerIp: { name: "otp-verify-ip", limit: 30, windowSeconds: 900 },
  mfaVerifyPerUser: { name: "mfa-verify-user", limit: 5, windowSeconds: 300 },
  mutationsPerSession: { name: "mutations-session", limit: 60, windowSeconds: 60 },
  // Generous: mobile carriers put many buyers behind one IP (carrier-grade NAT).
  exportPerUser: { name: "export-user", limit: 5, windowSeconds: 3600 },
  reportsPerIp: { name: "reports-ip", limit: 10, windowSeconds: 3600 },
  appealsPerUser: { name: "appeals-user", limit: 5, windowSeconds: 86_400 },
  faydaStartPerUser: { name: "fayda-start-user", limit: 5, windowSeconds: 3600 },
  uploadPerUser: { name: "upload-user", limit: 30, windowSeconds: 3600 },
  verifyPerIp: { name: "verify-ip", limit: 120, windowSeconds: 60 },
} as const satisfies Record<string, RateRule>;

export class RateLimitedError extends ApiError {
  constructor(readonly retryAfterSeconds: number) {
    super(HttpStatus.TOO_MANY_REQUESTS, "rate_limited", "Too many requests. Please wait and try again.");
  }
}

export const RATE_LIMIT_STORE = Symbol("RATE_LIMIT_STORE");

export function createRateLimitStore(config: AppConfig): RateLimitStore {
  if (config.redisUrl) {
    const redis = new Redis(config.redisUrl, { lazyConnect: false, maxRetriesPerRequest: 2, enableOfflineQueue: true });
    redis.on("error", (e: Error) => new Logger("redis").warn(`redis error: ${e.name}`));
    return new RedisRateLimitStore(redis);
  }
  return new MemoryRateLimitStore();
}

@Injectable()
export class RateLimiter implements OnModuleDestroy {
  constructor(@Inject(RATE_LIMIT_STORE) private readonly store: RateLimitStore) {}

  /**
   * Counts one attempt against `rule` for `subject` (an HMAC, never a raw phone/IP)
   * and throws 429 with Retry-After once the limit is exceeded.
   * If the store is unreachable we fail closed for these security-sensitive routes.
   */
  async consume(rule: RateRule, subject: string): Promise<void> {
    let res: { count: number; ttlSeconds: number };
    try {
      res = await this.store.hit(`rl:${rule.name}:${subject}`, rule.windowSeconds);
    } catch {
      throw new ApiError(HttpStatus.SERVICE_UNAVAILABLE, "unavailable", "Service temporarily unavailable.");
    }
    if (res.count > rule.limit) throw new RateLimitedError(res.ttlSeconds);
  }

  async onModuleDestroy() {
    await this.store.close();
  }
}
