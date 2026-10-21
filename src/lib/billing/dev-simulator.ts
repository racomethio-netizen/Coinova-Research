import { createHmac, randomUUID, timingSafeEqual } from "node:crypto";
import { getPaidPlan } from "@/config/plans";
import {
  NormalizedBillingEventSchema,
  WebhookVerificationError,
  type BillingEventType,
  type BillingProvider,
  type CheckoutRequest,
  type CheckoutSession,
  type NormalizedBillingEvent,
} from "./types";

/**
 * DEVELOPMENT BILLING SIMULATOR — never moves money.
 *
 * - Disabled when APP_ENV=production (enforced in env validation).
 * - All events are `livemode: false`. The database function
 *   `current_membership_tier()` ignores non-live subscriptions whenever
 *   app_config.require_livemode_subscriptions is true (the default), so a
 *   simulated payment cannot grant entitlements in a production database.
 * - Webhooks are signed with HMAC-SHA256 and go through exactly the same
 *   verification + processing path as a real provider.
 *
 * Signature header: `x-dev-billing-signature: t=<unix>,v1=<hex hmac of "t.body">`
 */

export const DEV_SIGNATURE_HEADER = "x-dev-billing-signature";
const TOLERANCE_SECONDS = 300;

export function signDevPayload(secret: string, body: string, timestamp = Math.floor(Date.now() / 1000)): string {
  const mac = createHmac("sha256", secret).update(`${timestamp}.${body}`).digest("hex");
  return `t=${timestamp},v1=${mac}`;
}

export function verifyDevSignature(secret: string, body: string, header: string | null, nowSeconds = Math.floor(Date.now() / 1000)): void {
  if (!header) throw new WebhookVerificationError("Missing signature header.");
  const parts = Object.fromEntries(header.split(",").map((p) => p.split("=", 2) as [string, string]));
  const t = Number(parts.t);
  const v1 = parts.v1;
  if (!Number.isInteger(t) || !v1 || !/^[a-f0-9]{64}$/.test(v1)) throw new WebhookVerificationError("Malformed signature header.");
  if (Math.abs(nowSeconds - t) > TOLERANCE_SECONDS) throw new WebhookVerificationError("Signature timestamp outside tolerance.");
  const expected = createHmac("sha256", secret).update(`${t}.${body}`).digest();
  const given = Buffer.from(v1, "hex");
  if (given.length !== expected.length || !timingSafeEqual(given, expected)) throw new WebhookVerificationError("Signature mismatch.");
}

export class DevBillingSimulator implements BillingProvider {
  readonly id = "dev_simulator";
  readonly livemode = false;

  constructor(
    private readonly secret: string,
    private readonly siteUrl: string,
  ) {}

  async createCheckout(req: CheckoutRequest): Promise<CheckoutSession> {
    const plan = getPaidPlan(req.planId);
    if (!plan) throw new Error("Unknown plan.");
    const sessionId = `devcs_${randomUUID()}`;
    return {
      provider: this.id,
      sessionId,
      url: new URL(`/dashboard/membership/dev-checkout?session=${encodeURIComponent(sessionId)}`, this.siteUrl).toString(),
      expiresAt: new Date(Date.now() + 30 * 60_000).toISOString(),
    };
  }

  async parseWebhook(rawBody: string, headers: Headers): Promise<NormalizedBillingEvent> {
    verifyDevSignature(this.secret, rawBody, headers.get(DEV_SIGNATURE_HEADER));
    let json: unknown;
    try {
      json = JSON.parse(rawBody);
    } catch {
      throw new WebhookVerificationError("Malformed JSON.");
    }
    const parsed = NormalizedBillingEventSchema.safeParse(json);
    if (!parsed.success) throw new WebhookVerificationError("Event failed schema validation.");
    if (parsed.data.provider !== this.id) throw new WebhookVerificationError("Event provider mismatch.");
    if (parsed.data.livemode) throw new WebhookVerificationError("Simulator events must not be livemode.");
    return parsed.data;
  }

  async cancelSubscription(): Promise<void> {
    // The simulator has no remote state; cancellation is delivered as a simulated webhook.
  }

  /** Build and sign a simulated event (used by the dev checkout page and tests). */
  buildSignedEvent(input: {
    type: BillingEventType;
    userId: string;
    planId: string;
    providerSubscriptionId: string;
    occurredAt?: Date;
    cancelAtPeriodEnd?: boolean;
    eventId?: string;
  }): { body: string; signature: string; event: NormalizedBillingEvent } {
    const plan = getPaidPlan(input.planId);
    if (!plan) throw new Error("Unknown plan.");
    const occurred = input.occurredAt ?? new Date();
    const periodEnd = new Date(occurred);
    if (plan.interval === "month") periodEnd.setUTCMonth(periodEnd.getUTCMonth() + 1);
    else periodEnd.setUTCFullYear(periodEnd.getUTCFullYear() + 1);
    const isPeriodEvent = input.type === "subscription.activated" || input.type === "subscription.renewed";
    const event: NormalizedBillingEvent = {
      provider: this.id,
      eventId: input.eventId ?? `devevt_${randomUUID()}`,
      type: input.type,
      occurredAt: occurred.toISOString(),
      livemode: false,
      userId: input.userId,
      planId: plan.id,
      providerSubscriptionId: input.providerSubscriptionId,
      providerCustomerId: `devcus_${input.userId.slice(0, 8)}`,
      currentPeriodStart: isPeriodEvent ? occurred.toISOString() : null,
      currentPeriodEnd: isPeriodEvent ? periodEnd.toISOString() : null,
      cancelAtPeriodEnd: input.cancelAtPeriodEnd,
      amountCents: isPeriodEvent || input.type === "charge.refunded" ? plan.priceCents : null,
      currency: isPeriodEvent || input.type === "charge.refunded" ? "USD" : null,
    };
    const body = JSON.stringify(event);
    return { body, signature: signDevPayload(this.secret, body), event };
  }
}
