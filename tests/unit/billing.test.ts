import { describe, expect, it } from "vitest";
import { DevBillingSimulator, DEV_SIGNATURE_HEADER, signDevPayload, verifyDevSignature } from "@/lib/billing/dev-simulator";
import { applyBillingEvent, resolveTier, subscriptionGrantsAccess } from "@/lib/billing/state";
import { WebhookVerificationError, type NormalizedBillingEvent, type SubscriptionRecord } from "@/lib/billing/types";

const SECRET = "a".repeat(40);
const USER = "11111111-1111-4111-8111-111111111111";
const sim = new DevBillingSimulator(SECRET, "http://localhost:3000");

function event(p: Partial<NormalizedBillingEvent> = {}): NormalizedBillingEvent {
  return {
    provider: "dev_simulator",
    eventId: "evt_1",
    type: "subscription.activated",
    occurredAt: "2026-10-01T00:00:00.000Z",
    livemode: false,
    userId: USER,
    planId: "premium_monthly",
    providerSubscriptionId: "sub_1",
    providerCustomerId: "cus_1",
    currentPeriodStart: "2026-10-01T00:00:00.000Z",
    currentPeriodEnd: "2026-11-01T00:00:00.000Z",
    amountCents: 1900,
    currency: "USD",
    ...p,
  };
}

function applyAll(events: NormalizedBillingEvent[]): SubscriptionRecord | null {
  let cur: SubscriptionRecord | null = null;
  for (const e of events) {
    const r = applyBillingEvent(cur, e);
    if (r.action === "upsert") cur = r.next;
  }
  return cur;
}

const opts = (now: string, requireLivemode = false) => ({ now: Date.parse(now), gracePeriodDays: 3, requireLivemode });

describe("webhook signature verification", () => {
  it("accepts a valid signature", async () => {
    const { body, signature } = sim.buildSignedEvent({ type: "subscription.activated", userId: USER, planId: "premium_monthly", providerSubscriptionId: "sub_1" });
    const ev = await sim.parseWebhook(body, new Headers({ [DEV_SIGNATURE_HEADER]: signature }));
    expect(ev.userId).toBe(USER);
    expect(ev.livemode).toBe(false);
  });

  it("rejects a missing, malformed, tampered, or wrongly keyed signature", async () => {
    const { body, signature } = sim.buildSignedEvent({ type: "subscription.activated", userId: USER, planId: "premium_monthly", providerSubscriptionId: "sub_1" });
    await expect(sim.parseWebhook(body, new Headers())).rejects.toBeInstanceOf(WebhookVerificationError);
    await expect(sim.parseWebhook(body, new Headers({ [DEV_SIGNATURE_HEADER]: "garbage" }))).rejects.toBeInstanceOf(WebhookVerificationError);
    await expect(sim.parseWebhook(body.replace("premium_monthly", "founding_yearly"), new Headers({ [DEV_SIGNATURE_HEADER]: signature }))).rejects.toThrow(/mismatch/);
    const other = signDevPayload("b".repeat(40), body);
    await expect(sim.parseWebhook(body, new Headers({ [DEV_SIGNATURE_HEADER]: other }))).rejects.toThrow(/mismatch/);
  });

  it("rejects old timestamps (replay protection)", () => {
    const body = "{}";
    const sig = signDevPayload(SECRET, body, 1_000);
    expect(() => verifyDevSignature(SECRET, body, sig, 1_000 + 301)).toThrow(/tolerance/);
  });

  it("rejects simulator events claiming livemode", async () => {
    const body = JSON.stringify(event({ livemode: true }));
    await expect(sim.parseWebhook(body, new Headers({ [DEV_SIGNATURE_HEADER]: signDevPayload(SECRET, body) }))).rejects.toThrow(/livemode/);
  });
});

describe("applyBillingEvent", () => {
  it("activates a subscription", () => {
    const s = applyAll([event()]);
    expect(s).toMatchObject({ status: "active", tier: "premium", currentPeriodEnd: "2026-11-01T00:00:00.000Z" });
  });

  it("ignores out-of-order (delayed) events", () => {
    const renewed = event({ eventId: "e2", type: "subscription.renewed", occurredAt: "2026-11-01T00:00:00.000Z", currentPeriodEnd: "2026-12-01T00:00:00.000Z" });
    const lateFailure = event({ eventId: "e3", type: "subscription.payment_failed", occurredAt: "2026-10-31T23:00:00.000Z", currentPeriodEnd: null });
    let cur = applyAll([event(), renewed]);
    const r = applyBillingEvent(cur, lateFailure);
    expect(r).toEqual({ action: "ignore", reason: "out_of_order" });
    cur = applyAll([event(), renewed, lateFailure]);
    expect(cur!.status).toBe("active");
  });

  it("marks past_due on failed payment; access continues through the grace period only", () => {
    const s = applyAll([event(), event({ eventId: "e2", type: "subscription.payment_failed", occurredAt: "2026-11-01T00:00:00.000Z" })])!;
    expect(s.status).toBe("past_due");
    expect(subscriptionGrantsAccess(s, opts("2026-11-02T00:00:00Z"))).toBe(true);
    expect(subscriptionGrantsAccess(s, opts("2026-11-05T00:00:00Z"))).toBe(false);
  });

  it("cancel at period end keeps access until period end, then expires", () => {
    const s = applyAll([event(), event({ eventId: "e2", type: "subscription.canceled", occurredAt: "2026-10-10T00:00:00.000Z", cancelAtPeriodEnd: true })])!;
    expect(s.status).toBe("active");
    expect(s.cancelAtPeriodEnd).toBe(true);
    expect(subscriptionGrantsAccess(s, opts("2026-10-20T00:00:00Z"))).toBe(true);
    expect(subscriptionGrantsAccess(s, opts("2026-11-01T00:00:01Z"))).toBe(false);
  });

  it("immediate cancellation and expiration end access", () => {
    const c = applyAll([event(), event({ eventId: "e2", type: "subscription.canceled", occurredAt: "2026-10-10T00:00:00.000Z" })])!;
    expect(c.status).toBe("canceled");
    expect(subscriptionGrantsAccess(c, opts("2026-10-11T00:00:00Z"))).toBe(false);
    const e = applyAll([event(), event({ eventId: "e2", type: "subscription.expired", occurredAt: "2026-11-01T00:00:00.000Z" })])!;
    expect(e.status).toBe("expired");
  });

  it("refund ends access", () => {
    const r = applyAll([event(), event({ eventId: "e2", type: "charge.refunded", occurredAt: "2026-10-05T00:00:00.000Z" })])!;
    expect(r.status).toBe("refunded");
    expect(subscriptionGrantsAccess(r, opts("2026-10-06T00:00:00Z"))).toBe(false);
  });

  it("ignores events for unknown plans, other users, or nonexistent subscriptions", () => {
    expect(applyBillingEvent(null, event({ planId: "free_lunch" }))).toMatchObject({ action: "ignore", reason: "unknown_plan" });
    expect(applyBillingEvent(null, event({ type: "subscription.payment_failed" }))).toMatchObject({ reason: "no_subscription" });
    const cur = applyAll([event()]);
    expect(applyBillingEvent(cur, event({ userId: "22222222-2222-4222-8222-222222222222", occurredAt: "2026-10-02T00:00:00Z" }))).toMatchObject({ reason: "user_mismatch" });
  });
});

describe("resolveTier", () => {
  it("returns free with no subscriptions and the highest active tier otherwise", () => {
    expect(resolveTier([], opts("2026-10-15T00:00:00Z"))).toBe("free");
    const subs = [
      { tier: "premium" as const, status: "active" as const, currentPeriodEnd: "2026-11-01T00:00:00Z", livemode: true },
      { tier: "founding" as const, status: "active" as const, currentPeriodEnd: "2027-10-01T00:00:00Z", livemode: true },
    ];
    expect(resolveTier(subs, opts("2026-10-15T00:00:00Z", true))).toBe("founding");
  });

  it("expired subscriptions grant free", () => {
    expect(resolveTier([{ tier: "premium", status: "active", currentPeriodEnd: "2026-10-01T00:00:00Z", livemode: true }], opts("2026-10-15T00:00:00Z"))).toBe("free");
  });

  it("test-mode (simulator) subscriptions grant nothing when livemode is required", () => {
    const sub = { tier: "premium" as const, status: "active" as const, currentPeriodEnd: "2027-01-01T00:00:00Z", livemode: false };
    expect(resolveTier([sub], opts("2026-10-15T00:00:00Z", true))).toBe("free");
    expect(resolveTier([sub], opts("2026-10-15T00:00:00Z", false))).toBe("premium");
  });
});
