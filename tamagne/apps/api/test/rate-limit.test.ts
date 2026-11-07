import { randomUUID } from "node:crypto";
import { Redis } from "ioredis";
import { describe, expect, it } from "vitest";
import { MemoryRateLimitStore, RateLimiter, RedisRateLimitStore } from "../src/security/rate-limit.js";

const rule = { name: "t", limit: 3, windowSeconds: 2 };

describe("memory store", () => {
  it("counts within a window and resets after it", async () => {
    const clock = { now: 0 };
    const limiter = new RateLimiter(new MemoryRateLimitStore(() => clock.now));
    for (let i = 0; i < 3; i++) await limiter.consume(rule, "a");
    await expect(limiter.consume(rule, "a")).rejects.toMatchObject({ code: "rate_limited", retryAfterSeconds: 2 });
    await limiter.consume(rule, "b"); // other subjects unaffected
    clock.now += 2001;
    await expect(limiter.consume(rule, "a")).resolves.toBeUndefined();
  });

  it("fails closed when the store is unavailable", async () => {
    const limiter = new RateLimiter({ hit: () => Promise.reject(new Error("down")), close: async () => {} });
    await expect(limiter.consume(rule, "a")).rejects.toMatchObject({ code: "unavailable" });
  });
});

// Runs against a real Redis when REDIS_TEST_URL is set (CI and `npm run test:redis`).
describe.skipIf(!process.env.REDIS_TEST_URL)("redis store", () => {
  it("shares counters atomically and expires them", async () => {
    const redis = new Redis(process.env.REDIS_TEST_URL!);
    const store = new RedisRateLimitStore(redis);
    const limiter = new RateLimiter(store);
    const subject = randomUUID();
    const results = await Promise.allSettled(Array.from({ length: 10 }, () => limiter.consume(rule, subject)));
    expect(results.filter((r) => r.status === "fulfilled")).toHaveLength(3);
    const ttl = await redis.ttl(`rl:t:${subject}`);
    expect(ttl).toBeGreaterThan(0);
    expect(ttl).toBeLessThanOrEqual(2);
    await new Promise((r) => setTimeout(r, 2100));
    await expect(limiter.consume(rule, subject)).resolves.toBeUndefined();
    await store.close();
  });
});
