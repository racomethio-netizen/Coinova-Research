import { describe, expect, it } from "vitest";
import { afterAttempt, MAX_DELIVERY_ATTEMPTS, nextAttemptDelayMs } from "@/lib/alerts/delivery";
import { AlertInputSchema, evaluateAlert, type AlertContext, type AlertState } from "@/lib/alerts/evaluate";

const NOW = "2026-10-01T12:00:00.000Z";
const alert = (p: Partial<AlertState> = {}): AlertState => ({
  id: "a1",
  type: "price_above",
  threshold: 100,
  isActive: true,
  triggerMode: "recurring",
  cooldownMinutes: 60,
  lastConditionMet: null,
  lastTriggeredAt: null,
  ...p,
});
const ctx = (value: number | null, p: Partial<AlertContext["observation"]> = {}): AlertContext => ({
  now: NOW,
  maxDataAgeMinutes: 30,
  observation: { value, observedAt: "2026-10-01T11:58:00.000Z", ...p },
});

describe("evaluateAlert", () => {
  it("triggers when price crosses above threshold", () => {
    const r = evaluateAlert(alert(), ctx(101));
    expect(r.outcome).toBe("triggered");
    expect(r.dedupeKey).toMatch(/^alert:a1:\d+$/);
  });

  it("does not trigger below threshold", () => {
    expect(evaluateAlert(alert(), ctx(99)).outcome).toBe("not_met");
  });

  it("price_below triggers at or below threshold", () => {
    expect(evaluateAlert(alert({ type: "price_below" }), ctx(100)).outcome).toBe("triggered");
  });

  it("is edge-triggered: no repeat while condition stays met", () => {
    const r = evaluateAlert(alert({ lastConditionMet: true, lastTriggeredAt: "2026-09-01T00:00:00Z" }), ctx(150));
    expect(r.outcome).toBe("suppressed");
    expect(r.dedupeKey).toBeNull();
  });

  it("re-arms after condition becomes false, then respects cooldown", () => {
    const recently = evaluateAlert(alert({ lastConditionMet: false, lastTriggeredAt: "2026-10-01T11:30:00Z" }), ctx(150));
    expect(recently.outcome).toBe("suppressed");
    expect(recently.conditionMet).toBe(false);
    const later = evaluateAlert(alert({ lastConditionMet: false, lastTriggeredAt: "2026-10-01T10:00:00Z" }), ctx(150));
    expect(later.outcome).toBe("triggered");
  });

  it("produces the same dedupe key for a retried evaluation in the same window", () => {
    const a = evaluateAlert(alert(), ctx(150));
    const b = evaluateAlert(alert(), { ...ctx(150), now: "2026-10-01T12:00:30.000Z" });
    expect(a.dedupeKey).toBe(b.dedupeKey);
  });

  it("skips disabled alerts", () => {
    expect(evaluateAlert(alert({ isActive: false }), ctx(150)).outcome).toBe("skipped");
  });

  it("skips on missing or stale data instead of guessing", () => {
    expect(evaluateAlert(alert(), ctx(null)).outcome).toBe("skipped");
    expect(evaluateAlert(alert(), ctx(150, { observedAt: "2026-10-01T10:00:00Z" })).reason).toMatch(/stale/);
    expect(evaluateAlert(alert(), ctx(150, { observedAt: null })).outcome).toBe("skipped");
  });

  it("once-mode alerts deactivate after triggering", () => {
    expect(evaluateAlert(alert({ triggerMode: "once" }), ctx(150)).deactivate).toBe(true);
  });

  it("score_change uses absolute change", () => {
    expect(evaluateAlert(alert({ type: "score_change", threshold: 5 }), ctx(-6)).outcome).toBe("triggered");
    expect(evaluateAlert(alert({ type: "score_change", threshold: 5 }), ctx(4)).outcome).toBe("not_met");
  });

  it("unlock_upcoming triggers only for events within the window", () => {
    const a = alert({ type: "unlock_upcoming", threshold: 7 });
    expect(evaluateAlert(a, ctx(null, { nextEventAt: "2026-10-05T00:00:00Z" })).outcome).toBe("triggered");
    expect(evaluateAlert(a, ctx(null, { nextEventAt: "2026-11-05T00:00:00Z" })).outcome).toBe("not_met");
    expect(evaluateAlert(a, ctx(null, { nextEventAt: null })).outcome).toBe("not_met");
  });
});

describe("AlertInputSchema", () => {
  it("requires a coin for coin alerts and a portfolio for portfolio alerts", () => {
    expect(AlertInputSchema.safeParse({ type: "price_above", threshold: 10, channels: ["in_app"] }).success).toBe(false);
    expect(AlertInputSchema.safeParse({ type: "portfolio_value_above", threshold: 10, channels: ["in_app"] }).success).toBe(false);
    expect(AlertInputSchema.safeParse({ type: "price_above", coinId: "bitcoin", threshold: 10, channels: ["in_app"] }).success).toBe(true);
  });
  it("rejects invalid thresholds, channels and cooldowns", () => {
    expect(AlertInputSchema.safeParse({ type: "price_above", coinId: "bitcoin", threshold: -1, channels: ["in_app"] }).success).toBe(false);
    expect(AlertInputSchema.safeParse({ type: "price_above", coinId: "bitcoin", threshold: 1, channels: ["sms"] }).success).toBe(false);
    expect(AlertInputSchema.safeParse({ type: "price_above", coinId: "bitcoin", threshold: 1, channels: [] }).success).toBe(false);
    expect(AlertInputSchema.safeParse({ type: "price_above", coinId: "bitcoin", threshold: 1, channels: ["in_app"], cooldownMinutes: 1 }).success).toBe(false);
    expect(AlertInputSchema.safeParse({ type: "unlock_upcoming", coinId: "bitcoin", threshold: 1.5, channels: ["in_app"] }).success).toBe(false);
  });
  it("de-duplicates channels", () => {
    const r = AlertInputSchema.parse({ type: "price_above", coinId: "bitcoin", threshold: 1, channels: ["email", "email"] });
    expect(r.channels).toEqual(["email"]);
  });
});

describe("delivery retry policy", () => {
  it("backs off exponentially, capped at one hour", () => {
    expect(nextAttemptDelayMs(1)).toBe(60_000);
    expect(nextAttemptDelayMs(2)).toBe(120_000);
    expect(nextAttemptDelayMs(20)).toBe(3_600_000);
  });
  it("marks sent on success", () => {
    expect(afterAttempt(0, { ok: true }, 0)).toMatchObject({ status: "sent", attempts: 1 });
  });
  it("retries transient failures until the attempt limit, then fails", () => {
    const r1 = afterAttempt(0, { ok: false, error: "timeout" }, 0);
    expect(r1.status).toBe("pending");
    expect(r1.nextAttemptAt).not.toBeNull();
    const last = afterAttempt(MAX_DELIVERY_ATTEMPTS - 1, { ok: false, error: "timeout" }, 0);
    expect(last.status).toBe("failed");
  });
  it("does not retry permanent failures", () => {
    expect(afterAttempt(0, { ok: false, error: "chat not found", permanent: true }, 0).status).toBe("failed");
  });
});
