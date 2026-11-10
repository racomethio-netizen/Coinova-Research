import { describe, expect, it } from "vitest";
import { applyBillingEvent } from "@/lib/billing/state";
import { WebhookVerificationError, type BillingEventType, type BillingProvider } from "@/lib/billing/types";

/**
 * Conformance suite every BillingProvider adapter must pass (docs/BILLING.md).
 * A real provider (decision D9) gets a harness that produces genuinely signed
 * test payloads in that provider's format; it must then behave exactly like this.
 */
export interface ProviderHarness {
  provider: BillingProvider;
  /** A correctly signed webhook (raw body + headers) for the given event. */
  signed(input: { type: BillingEventType; userId: string; planId: string; providerSubscriptionId: string; eventId?: string }): { body: string; headers: Headers };
  /** The same payload signed with the wrong secret. */
  wrongSecret(body: string): Headers;
  /** A signature whose timestamp is outside the replay tolerance, or null if the provider has no timestamps. */
  stale(body: string): Headers | null;
}

const USER = "22222222-2222-4222-8222-222222222222";

export function describeBillingProviderConformance(name: string, makeHarness: () => ProviderHarness) {
  describe(`BillingProvider conformance: ${name}`, () => {
    const h = makeHarness();
    const base = { type: "subscription.activated" as const, userId: USER, planId: "premium_monthly", providerSubscriptionId: "sub_conf_1" };

    it("accepts a correctly signed event and normalizes it", async () => {
      const { body, headers } = h.signed({ ...base, eventId: "evt_conf_1" });
      const ev = await h.provider.parseWebhook(body, headers);
      expect(ev).toMatchObject({ provider: h.provider.id, eventId: "evt_conf_1", type: "subscription.activated", userId: USER, planId: "premium_monthly", livemode: h.provider.livemode });
    });

    it("rejects missing, wrong, and tampered signatures without parsing the event", async () => {
      const { body, headers } = h.signed(base);
      await expect(h.provider.parseWebhook(body, new Headers())).rejects.toBeInstanceOf(WebhookVerificationError);
      await expect(h.provider.parseWebhook(body, h.wrongSecret(body))).rejects.toBeInstanceOf(WebhookVerificationError);
      await expect(h.provider.parseWebhook(body.replace(USER, "33333333-3333-4333-8333-333333333333"), headers)).rejects.toBeInstanceOf(WebhookVerificationError);
    });

    it("rejects replays outside the timestamp tolerance", async () => {
      const { body } = h.signed(base);
      const stale = h.stale(body);
      if (stale) await expect(h.provider.parseWebhook(body, stale)).rejects.toBeInstanceOf(WebhookVerificationError);
    });

    it("produces events the state machine applies to the right plan and tier", async () => {
      const { body, headers } = h.signed(base);
      const r = applyBillingEvent(null, await h.provider.parseWebhook(body, headers));
      expect(r.action).toBe("upsert");
      if (r.action === "upsert") expect(r.next).toMatchObject({ planId: "premium_monthly", tier: "premium", status: "active", userId: USER });
    });

    it("creates checkout sessions only for active plans", async () => {
      const s = await h.provider.createCheckout({ userId: USER, email: null, planId: "pro_monthly", successUrl: "https://x.invalid/ok", cancelUrl: "https://x.invalid/cancel" } as never);
      expect(s.provider).toBe(h.provider.id);
      expect(Date.parse(s.expiresAt)).toBeGreaterThan(Date.now());
      await expect(h.provider.createCheckout({ userId: USER, email: null, planId: "founding_yearly", successUrl: "", cancelUrl: "" } as never)).rejects.toThrow();
    });
  });
}
