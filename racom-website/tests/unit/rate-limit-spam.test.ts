import { describe, expect, it } from "vitest";
import { createRateLimiter } from "@/lib/contact/rate-limit";
import { MAX_FORM_AGE_MS, MIN_FILL_MS, checkSpam } from "@/lib/contact/spam";

describe("createRateLimiter", () => {
  it("allows up to max requests per window, then blocks with Retry-After", () => {
    const check = createRateLimiter({ max: 2, windowMs: 60_000 });
    expect(check("ip", 0).allowed).toBe(true);
    expect(check("ip", 1).allowed).toBe(true);
    const blocked = check("ip", 2);
    expect(blocked.allowed).toBe(false);
    expect(blocked.retryAfterSeconds).toBe(60);
  });

  it("tracks keys independently and resets after the window", () => {
    const check = createRateLimiter({ max: 1, windowMs: 1_000 });
    expect(check("a", 0).allowed).toBe(true);
    expect(check("b", 0).allowed).toBe(true);
    expect(check("a", 500).allowed).toBe(false);
    expect(check("a", 1_000).allowed).toBe(true);
  });

  it("bounds memory by evicting old keys", () => {
    const check = createRateLimiter({ max: 1, windowMs: 60_000, maxKeys: 2 });
    check("a", 0);
    check("b", 0);
    check("c", 0); // evicts "a"
    expect(check("a", 1).allowed).toBe(true);
  });
});

describe("checkSpam", () => {
  const now = 1_000_000_000;
  it("passes a normal human submission", () => {
    expect(checkSpam({ website: "", startedAt: now - 30_000 }, now)).toEqual({ ok: true });
  });
  it("flags a filled honeypot", () => {
    expect(checkSpam({ website: "http://spam", startedAt: now - 30_000 }, now)).toEqual({
      ok: false,
      reason: "honeypot",
    });
  });
  it("flags submissions faster than a person could type", () => {
    expect(checkSpam({ startedAt: now - MIN_FILL_MS + 1 }, now)).toEqual({ ok: false, reason: "too_fast" });
    expect(checkSpam({ startedAt: now + 5_000 }, now)).toEqual({ ok: false, reason: "too_fast" });
  });
  it("flags stale forms", () => {
    expect(checkSpam({ startedAt: now - MAX_FORM_AGE_MS - 1 }, now)).toEqual({ ok: false, reason: "stale" });
  });
});
