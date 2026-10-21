import { z } from "zod";
import type { MembershipTier } from "@/config/plans";

export const BILLING_EVENT_TYPES = [
  "subscription.activated",
  "subscription.renewed",
  "subscription.payment_failed",
  "subscription.canceled",
  "subscription.reactivated",
  "subscription.expired",
  "charge.refunded",
] as const;
export type BillingEventType = (typeof BILLING_EVENT_TYPES)[number];

/** Provider-neutral billing event, produced only by a provider's verified webhook parser. */
export const NormalizedBillingEventSchema = z.object({
  provider: z.string().min(1).max(40),
  eventId: z.string().min(1).max(200),
  type: z.enum(BILLING_EVENT_TYPES),
  occurredAt: z.iso.datetime(),
  livemode: z.boolean(),
  userId: z.uuid(),
  planId: z.string().min(1).max(60),
  providerSubscriptionId: z.string().min(1).max(200),
  providerCustomerId: z.string().max(200).nullable(),
  currentPeriodStart: z.iso.datetime().nullable(),
  currentPeriodEnd: z.iso.datetime().nullable(),
  /** For cancellations: whether access continues until period end. */
  cancelAtPeriodEnd: z.boolean().optional(),
  amountCents: z.number().int().nonnegative().nullable(),
  currency: z.string().length(3).nullable(),
});
export type NormalizedBillingEvent = z.infer<typeof NormalizedBillingEventSchema>;

export type SubscriptionStatus = "incomplete" | "trialing" | "active" | "past_due" | "canceled" | "expired" | "refunded";

export interface SubscriptionRecord {
  userId: string;
  planId: string;
  tier: Exclude<MembershipTier, "free">;
  status: SubscriptionStatus;
  provider: string;
  providerSubscriptionId: string;
  providerCustomerId: string | null;
  livemode: boolean;
  currentPeriodStart: string | null;
  currentPeriodEnd: string | null;
  cancelAtPeriodEnd: boolean;
  canceledAt: string | null;
  endedAt: string | null;
  lastEventAt: string;
}

export interface CheckoutRequest {
  userId: string;
  userEmail: string | null;
  planId: string;
  successUrl: string;
  cancelUrl: string;
}

export interface CheckoutSession {
  provider: string;
  sessionId: string;
  url: string;
  expiresAt: string;
}

export class WebhookVerificationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "WebhookVerificationError";
  }
}

/**
 * A billing provider adapter. Implementations MUST verify webhook
 * authenticity in `parseWebhook` and throw WebhookVerificationError on any
 * failure. Entitlements are never granted from a browser redirect.
 */
export interface BillingProvider {
  readonly id: string;
  readonly livemode: boolean;
  createCheckout(req: CheckoutRequest): Promise<CheckoutSession>;
  parseWebhook(rawBody: string, headers: Headers): Promise<NormalizedBillingEvent>;
  /** Request cancellation at period end. Final state arrives via webhook. */
  cancelSubscription(providerSubscriptionId: string): Promise<void>;
}
