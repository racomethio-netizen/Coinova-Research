import { getPaidPlan, TIER_RANK, type MembershipTier } from "@/config/plans";
import type { NormalizedBillingEvent, SubscriptionRecord, SubscriptionStatus } from "./types";

/**
 * Pure subscription state machine.
 *
 * Out-of-order safety: each subscription stores `lastEventAt`. An event that
 * occurred before it is recorded but ignored, so a delayed "payment_failed"
 * cannot override a newer "renewed".
 */

export type ApplyResult =
  | { action: "upsert"; next: SubscriptionRecord }
  | { action: "ignore"; reason: "out_of_order" | "unknown_plan" | "no_subscription" | "user_mismatch" };

export function applyBillingEvent(current: SubscriptionRecord | null, ev: NormalizedBillingEvent): ApplyResult {
  const plan = getPaidPlan(ev.planId);
  if (!plan) return { action: "ignore", reason: "unknown_plan" };
  if (current && current.userId !== ev.userId) return { action: "ignore", reason: "user_mismatch" };
  if (current && Date.parse(ev.occurredAt) < Date.parse(current.lastEventAt)) return { action: "ignore", reason: "out_of_order" };

  const base: SubscriptionRecord =
    current ??
    ({
      userId: ev.userId,
      planId: plan.id,
      tier: plan.tier,
      status: "incomplete",
      provider: ev.provider,
      providerSubscriptionId: ev.providerSubscriptionId,
      providerCustomerId: ev.providerCustomerId,
      livemode: ev.livemode,
      currentPeriodStart: null,
      currentPeriodEnd: null,
      cancelAtPeriodEnd: false,
      canceledAt: null,
      endedAt: null,
      lastEventAt: ev.occurredAt,
    } satisfies SubscriptionRecord);

  const next: SubscriptionRecord = {
    ...base,
    planId: plan.id,
    tier: plan.tier,
    providerCustomerId: ev.providerCustomerId ?? base.providerCustomerId,
    lastEventAt: ev.occurredAt,
  };

  switch (ev.type) {
    case "subscription.activated":
    case "subscription.renewed":
    case "subscription.reactivated":
      if (!current && ev.type !== "subscription.activated") {
        // A renewal for a subscription we never saw activate: accept it only with a valid period.
        if (!ev.currentPeriodEnd) return { action: "ignore", reason: "no_subscription" };
      }
      next.status = "active";
      next.currentPeriodStart = ev.currentPeriodStart ?? next.currentPeriodStart;
      next.currentPeriodEnd = ev.currentPeriodEnd ?? next.currentPeriodEnd;
      next.cancelAtPeriodEnd = false;
      next.canceledAt = null;
      next.endedAt = null;
      break;
    case "subscription.payment_failed":
      if (!current) return { action: "ignore", reason: "no_subscription" };
      next.status = "past_due";
      break;
    case "subscription.canceled":
      if (!current) return { action: "ignore", reason: "no_subscription" };
      next.canceledAt = ev.occurredAt;
      if (ev.cancelAtPeriodEnd) {
        // Access continues until currentPeriodEnd; status stays active.
        next.cancelAtPeriodEnd = true;
      } else {
        next.status = "canceled";
        next.endedAt = ev.occurredAt;
      }
      break;
    case "subscription.expired":
      if (!current) return { action: "ignore", reason: "no_subscription" };
      next.status = "expired";
      next.endedAt = ev.occurredAt;
      break;
    case "charge.refunded":
      if (!current) return { action: "ignore", reason: "no_subscription" };
      // Policy: a refund ends access immediately.
      next.status = "refunded";
      next.endedAt = ev.occurredAt;
      break;
  }
  return { action: "upsert", next };
}

export interface EntitlementOptions {
  now: number;
  gracePeriodDays: number;
  /** When true (production), test-mode subscriptions grant nothing. */
  requireLivemode: boolean;
}

/** Whether a single subscription currently grants its tier. Mirrors SQL `subscription_grants_access`. */
export function subscriptionGrantsAccess(
  s: Pick<SubscriptionRecord, "status" | "currentPeriodEnd" | "livemode">,
  opts: EntitlementOptions,
): boolean {
  if (opts.requireLivemode && !s.livemode) return false;
  if (!s.currentPeriodEnd) return false;
  const end = Date.parse(s.currentPeriodEnd);
  if (!Number.isFinite(end)) return false;
  const status: SubscriptionStatus = s.status;
  if (status === "active" || status === "trialing") return opts.now < end;
  if (status === "past_due") return opts.now < end + opts.gracePeriodDays * 86_400_000;
  return false;
}

export function resolveTier(
  subs: readonly Pick<SubscriptionRecord, "status" | "currentPeriodEnd" | "livemode" | "tier">[],
  opts: EntitlementOptions,
): MembershipTier {
  let best: MembershipTier = "free";
  for (const s of subs) {
    if (subscriptionGrantsAccess(s, opts) && TIER_RANK[s.tier] > TIER_RANK[best]) best = s.tier;
  }
  return best;
}

export function hasTier(actual: MembershipTier, required: MembershipTier): boolean {
  return TIER_RANK[actual] >= TIER_RANK[required];
}
