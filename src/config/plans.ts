/**
 * Membership plan catalog — the single source of truth for plan definitions,
 * display prices, per-tier limits, and per-tier feature entitlements.
 *
 * PRICES ARE ILLUSTRATIVE and must be reviewed before launch. When a real
 * billing provider is configured, the provider's price records are the
 * authority for what is charged; `providerPriceIds` maps each plan to them.
 *
 * The database tables `membership_plans` and `plan_features` mirror this file
 * (seeded by migrations and kept in sync by `npm run job -- sync-plans`) so
 * that Row Level Security policies and limit triggers can read them.
 *
 * Tiers: free < pro < premium. The legacy "founding" tier is retired; any
 * legacy row is treated as premium (see `normalizeTier` and the SQL function
 * `membership_tier_for`).
 */

export type MembershipTier = "free" | "pro" | "premium";
export type BillingInterval = "month" | "year";

export const TIER_RANK: Record<MembershipTier, number> = {
  free: 0,
  pro: 1,
  premium: 2,
};

export const TIER_LABEL: Record<MembershipTier, string> = {
  free: "Free",
  pro: "Pro",
  premium: "Premium",
};

/** Map any tier string from storage to a current tier. Unknown values grant nothing. */
export function normalizeTier(value: unknown): MembershipTier {
  if (value === "founding") return "premium";
  return typeof value === "string" && Object.prototype.hasOwnProperty.call(TIER_RANK, value) ? (value as MembershipTier) : "free";
}

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
  pro: {
    watchlists: 5,
    watchlistItems: 50,
    portfolios: 2,
    alerts: 25,
    notificationsPerHour: 10,
    aiRequestsPerDay: 20,
  },
  premium: {
    watchlists: 10,
    watchlistItems: 100,
    portfolios: 5,
    alerts: 50,
    notificationsPerHour: 20,
    aiRequestsPerDay: 50,
  },
};

/**
 * Feature entitlements (NEXUS). Checked server-side with `hasFeature()` and in
 * SQL with `public.has_feature(key)`. A feature listed here is an *entitlement*,
 * not a claim that it is built; features behind legal gates (signals) also
 * require their gate (docs/nexus/COMPLIANCE.md).
 */
export const FEATURE_KEYS = [
  "charts.basic",
  "analytics.advanced",
  "signals.history",
  "signals.alerts",
  "performance.reports",
  "models.analytics",
  "backtesting",
  "strategies.custom",
  "research.advanced_tools",
] as const;
export type FeatureKey = (typeof FEATURE_KEYS)[number];

const FEATURE_MIN_TIER: Record<FeatureKey, MembershipTier> = {
  "charts.basic": "free",
  "analytics.advanced": "pro",
  "signals.history": "pro",
  "signals.alerts": "pro",
  "performance.reports": "pro",
  "models.analytics": "premium",
  backtesting: "premium",
  "strategies.custom": "premium",
  "research.advanced_tools": "premium",
};

export const PLAN_FEATURES: Record<MembershipTier, Record<FeatureKey, boolean>> = Object.fromEntries(
  (Object.keys(TIER_RANK) as MembershipTier[]).map((tier) => [
    tier,
    Object.fromEntries(FEATURE_KEYS.map((k) => [k, TIER_RANK[tier] >= TIER_RANK[FEATURE_MIN_TIER[k]]])),
  ]),
) as Record<MembershipTier, Record<FeatureKey, boolean>>;

export function tierHasFeature(tier: MembershipTier, feature: FeatureKey): boolean {
  return PLAN_FEATURES[tier][feature];
}

export function minimumTierFor(feature: FeatureKey): MembershipTier {
  return FEATURE_MIN_TIER[feature];
}

export interface PaidPlan {
  id: "pro_monthly" | "pro_yearly" | "premium_monthly" | "premium_yearly";
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
    id: "pro_monthly",
    tier: "pro",
    name: "Pro — Monthly",
    interval: "month",
    priceCents: 900,
    currency: "USD",
    description: "All research scores, portfolios, alerts, and exports.",
    providerPriceIds: {},
    isActive: true,
  },
  {
    id: "pro_yearly",
    tier: "pro",
    name: "Pro — Yearly",
    interval: "year",
    priceCents: 7900,
    currency: "USD",
    description: "Everything in Pro, billed annually.",
    providerPriceIds: {},
    isActive: true,
  },
  {
    id: "premium_monthly",
    tier: "premium",
    name: "Premium — Monthly",
    interval: "month",
    priceCents: 1900,
    currency: "USD",
    description: "Everything in Pro with higher limits and all Premium research.",
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
] as const;

/** Plans that existed before and must stay inactive (sync-plans deactivates them). */
export const RETIRED_PLAN_IDS = ["founding_yearly"] as const;

export type PaidPlanId = PaidPlan["id"];
export const PAID_PLAN_IDS = PAID_PLANS.map((p) => p.id) as [PaidPlanId, ...PaidPlanId[]];

export function getPaidPlan(id: string): PaidPlan | undefined {
  return PAID_PLANS.find((p) => p.id === id && p.isActive);
}

/** Feature matrix used on the pricing page. Keep claims accurate to what is built. */
export const FEATURE_MATRIX: ReadonlyArray<{
  feature: string;
  free: string | boolean;
  pro: string | boolean;
  premium: string | boolean;
}> = [
  { feature: "Public market pages and charts", free: true, pro: true, premium: true },
  { feature: "Research articles", free: "Free articles", pro: "Free and Pro articles", premium: "All articles" },
  { feature: "Research scores", free: "Sample coins", pro: "All covered coins", premium: "All covered coins" },
  { feature: "Score history and breakdown", free: false, pro: true, premium: true },
  { feature: "Watchlists", free: `${TIER_LIMITS.free.watchlists} list, ${TIER_LIMITS.free.watchlistItems} coins`, pro: `${TIER_LIMITS.pro.watchlists} lists`, premium: `${TIER_LIMITS.premium.watchlists} lists` },
  { feature: "Portfolios", free: false, pro: `Up to ${TIER_LIMITS.pro.portfolios}`, premium: `Up to ${TIER_LIMITS.premium.portfolios}` },
  { feature: "Transaction CSV export", free: false, pro: true, premium: true },
  { feature: "Configurable alerts", free: false, pro: `Up to ${TIER_LIMITS.pro.alerts}`, premium: `Up to ${TIER_LIMITS.premium.alerts}` },
  { feature: "AI research assistant (daily requests)", free: String(TIER_LIMITS.free.aiRequestsPerDay), pro: String(TIER_LIMITS.pro.aiRequestsPerDay), premium: String(TIER_LIMITS.premium.aiRequestsPerDay) },
];

export function formatPrice(priceCents: number, currency = "USD"): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    minimumFractionDigits: priceCents % 100 === 0 ? 0 : 2,
  }).format(priceCents / 100);
}
