/**
 * Membership plan catalog — the single source of truth for plan definitions,
 * display prices, and per-tier limits.
 *
 * PRICES ARE ILLUSTRATIVE and must be reviewed before launch. When a real
 * billing provider is configured, the provider's price records are the
 * authority for what is charged; `providerPriceIds` maps each plan to them.
 *
 * The database table `membership_plans` mirrors this file (seeded by migration
 * 0003 and kept in sync by `npm run job -- sync-plans`) so that Row Level
 * Security policies and limit triggers can read tier limits.
 */

export type MembershipTier = "free" | "premium" | "founding";
export type BillingInterval = "month" | "year";

export const TIER_RANK: Record<MembershipTier, number> = {
  free: 0,
  premium: 1,
  founding: 2,
};

export interface TierLimits {
  watchlists: number;
  watchlistItems: number;
  portfolios: number;
  alerts: number;
  /** Max notifications delivered per user per rolling hour (all channels). */
  notificationsPerHour: number;
  /** Max AI assistant requests per user per UTC day. */
  aiRequestsPerDay: number;
}

export const TIER_LIMITS: Record<MembershipTier, TierLimits> = {
  free: {
    watchlists: 1,
    watchlistItems: 10,
    portfolios: 0,
    alerts: 0,
    notificationsPerHour: 0,
    aiRequestsPerDay: 3,
  },
  premium: {
    watchlists: 10,
    watchlistItems: 100,
    portfolios: 5,
    alerts: 50,
    notificationsPerHour: 20,
    aiRequestsPerDay: 50,
  },
  founding: {
    watchlists: 25,
    watchlistItems: 250,
    portfolios: 20,
    alerts: 200,
    notificationsPerHour: 60,
    aiRequestsPerDay: 150,
  },
};

export interface PaidPlan {
  id: "premium_monthly" | "premium_yearly" | "founding_yearly";
  tier: Exclude<MembershipTier, "free">;
  name: string;
  interval: BillingInterval;
  /** Price in the smallest currency unit (cents). Illustrative. */
  priceCents: number;
  currency: "USD";
  description: string;
  /** Provider price identifiers, filled in when a real provider is configured. */
  providerPriceIds: Partial<Record<string, string>>;
  isActive: boolean;
}

export const PAID_PLANS: readonly PaidPlan[] = [
  {
    id: "premium_monthly",
    tier: "premium",
    name: "Premium — Monthly",
    interval: "month",
    priceCents: 1900,
    currency: "USD",
    description: "Full research access, portfolios, alerts, and exports.",
    providerPriceIds: {},
    isActive: true,
  },
  {
    id: "premium_yearly",
    tier: "premium",
    name: "Premium — Yearly",
    interval: "year",
    priceCents: 16000,
    currency: "USD",
    description: "Everything in Premium, billed annually.",
    providerPriceIds: {},
    isActive: true,
  },
  {
    id: "founding_yearly",
    tier: "founding",
    name: "Founding Member — Yearly",
    interval: "year",
    priceCents: 30000,
    currency: "USD",
    description:
      "All Premium features with higher limits, additional research features, and priority support.",
    providerPriceIds: {},
    isActive: true,
  },
] as const;

export type PaidPlanId = PaidPlan["id"];

export function getPaidPlan(id: string): PaidPlan | undefined {
  return PAID_PLANS.find((p) => p.id === id && p.isActive);
}

/** Feature matrix used on the pricing page. Keep claims accurate to what is built. */
export const FEATURE_MATRIX: ReadonlyArray<{
  feature: string;
  free: string | boolean;
  premium: string | boolean;
  founding: string | boolean;
}> = [
  { feature: "Public market pages and charts", free: true, premium: true, founding: true },
  { feature: "Research articles", free: "Free articles", premium: "All Premium articles", founding: "All articles" },
  { feature: "Research scores", free: "Sample coins", premium: "All covered coins", founding: "All covered coins" },
  { feature: "Score history and breakdown", free: false, premium: true, founding: true },
  { feature: "Watchlists", free: `${TIER_LIMITS.free.watchlists} list, ${TIER_LIMITS.free.watchlistItems} coins`, premium: `${TIER_LIMITS.premium.watchlists} lists`, founding: `${TIER_LIMITS.founding.watchlists} lists` },
  { feature: "Portfolios", free: false, premium: `Up to ${TIER_LIMITS.premium.portfolios}`, founding: `Up to ${TIER_LIMITS.founding.portfolios}` },
  { feature: "Transaction CSV export", free: false, premium: true, founding: true },
  { feature: "Configurable alerts", free: false, premium: `Up to ${TIER_LIMITS.premium.alerts}`, founding: `Up to ${TIER_LIMITS.founding.alerts}` },
  { feature: "AI research assistant (daily requests)", free: String(TIER_LIMITS.free.aiRequestsPerDay), premium: String(TIER_LIMITS.premium.aiRequestsPerDay), founding: String(TIER_LIMITS.founding.aiRequestsPerDay) },
  { feature: "Priority support", free: false, premium: false, founding: true },
  { feature: "Research Q&A events (when scheduled)", free: false, premium: false, founding: true },
];

export function formatPrice(priceCents: number, currency = "USD"): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    minimumFractionDigits: priceCents % 100 === 0 ? 0 : 2,
  }).format(priceCents / 100);
}
