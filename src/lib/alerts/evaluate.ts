import { z } from "zod";

/**
 * Pure alert evaluation.
 *
 * Alerts are EDGE-TRIGGERED: an alert fires when its condition becomes true
 * (previous state false or unknown) — not on every evaluation while the
 * condition stays true. Recurring alerts re-arm once the condition is false
 * again, and are further limited by a cooldown. Combined with the
 * notification `dedupe_key` unique constraint, this prevents duplicate sends
 * even if the evaluation job is retried.
 */

export const ALERT_TYPES = [
  "price_above",
  "price_below",
  "pct_change_24h_above",
  "pct_change_24h_below",
  "volume_change_24h_above",
  "score_change",
  "unlock_upcoming",
  "portfolio_value_above",
  "portfolio_value_below",
] as const;
export type AlertType = (typeof ALERT_TYPES)[number];

export const COIN_ALERT_TYPES: readonly AlertType[] = ["price_above", "price_below", "pct_change_24h_above", "pct_change_24h_below", "volume_change_24h_above", "score_change", "unlock_upcoming"];
export const PORTFOLIO_ALERT_TYPES: readonly AlertType[] = ["portfolio_value_above", "portfolio_value_below"];

export const ALERT_TYPE_LABELS: Record<AlertType, string> = {
  price_above: "Price rises above",
  price_below: "Price falls below",
  pct_change_24h_above: "24h change rises above (%)",
  pct_change_24h_below: "24h change falls below (%)",
  volume_change_24h_above: "24h volume up by at least (%)",
  score_change: "Research score changes by at least (points)",
  unlock_upcoming: "Verified token unlock within (days)",
  portfolio_value_above: "Portfolio value rises above (USD)",
  portfolio_value_below: "Portfolio value falls below (USD)",
};

export const CHANNELS = ["in_app", "email", "telegram"] as const;
export type Channel = (typeof CHANNELS)[number];

export const AlertInputSchema = z
  .object({
    type: z.enum(ALERT_TYPES),
    coinId: z.string().regex(/^[a-z0-9][a-z0-9-]{0,99}$/).nullable().optional(),
    portfolioId: z.uuid().nullable().optional(),
    threshold: z.number().finite(),
    channels: z.array(z.enum(CHANNELS)).min(1).max(3).transform((c) => [...new Set(c)]),
    triggerMode: z.enum(["once", "recurring"]).default("recurring"),
    cooldownMinutes: z.number().int().min(5).max(10_080).default(60),
    isActive: z.boolean().default(true),
  })
  .superRefine((v, ctx) => {
    if (COIN_ALERT_TYPES.includes(v.type) && !v.coinId) ctx.addIssue({ code: "custom", path: ["coinId"], message: "A coin is required for this alert type." });
    if (PORTFOLIO_ALERT_TYPES.includes(v.type) && !v.portfolioId) ctx.addIssue({ code: "custom", path: ["portfolioId"], message: "A portfolio is required for this alert type." });
    if ((v.type === "price_above" || v.type === "price_below" || PORTFOLIO_ALERT_TYPES.includes(v.type)) && v.threshold <= 0) {
      ctx.addIssue({ code: "custom", path: ["threshold"], message: "Threshold must be greater than zero." });
    }
    if ((v.type === "volume_change_24h_above" || v.type === "score_change") && v.threshold <= 0) {
      ctx.addIssue({ code: "custom", path: ["threshold"], message: "Threshold must be greater than zero." });
    }
    if (v.type === "unlock_upcoming" && (!Number.isInteger(v.threshold) || v.threshold < 1 || v.threshold > 365)) {
      ctx.addIssue({ code: "custom", path: ["threshold"], message: "Window must be a whole number of days between 1 and 365." });
    }
    if ((v.type === "pct_change_24h_above" || v.type === "pct_change_24h_below") && Math.abs(v.threshold) > 1000) {
      ctx.addIssue({ code: "custom", path: ["threshold"], message: "Percentage threshold out of range." });
    }
  });
export type AlertInput = z.infer<typeof AlertInputSchema>;

export interface AlertState {
  id: string;
  type: AlertType;
  threshold: number;
  isActive: boolean;
  triggerMode: "once" | "recurring";
  cooldownMinutes: number;
  lastConditionMet: boolean | null;
  lastTriggeredAt: string | null;
}

export interface AlertContext {
  now: string;
  /** Maximum age of observations before they are considered stale. */
  maxDataAgeMinutes: number;
  observation: {
    value: number | null;
    /** When `value` was observed (provider time). */
    observedAt: string | null;
    /** For unlock alerts: the next verified unlock date, if any. */
    nextEventAt?: string | null;
  };
}

export type EvaluationOutcome = "triggered" | "not_met" | "suppressed" | "skipped";

export interface EvaluationResult {
  outcome: EvaluationOutcome;
  conditionMet: boolean | null;
  observedValue: number | null;
  reason: string;
  /** Deterministic key used to prevent duplicate notifications. */
  dedupeKey: string | null;
  /** Whether the alert should be deactivated after this evaluation. */
  deactivate: boolean;
}

function conditionMet(type: AlertType, value: number, threshold: number, ctx: AlertContext): boolean {
  switch (type) {
    case "price_above":
    case "pct_change_24h_above":
    case "volume_change_24h_above":
    case "portfolio_value_above":
      return value >= threshold;
    case "price_below":
    case "pct_change_24h_below":
    case "portfolio_value_below":
      return value <= threshold;
    case "score_change":
      return Math.abs(value) >= threshold;
    case "unlock_upcoming": {
      const at = ctx.observation.nextEventAt ? Date.parse(ctx.observation.nextEventAt) : NaN;
      if (!Number.isFinite(at)) return false;
      const days = (at - Date.parse(ctx.now)) / 86_400_000;
      return days >= 0 && days <= threshold;
    }
  }
}

export function evaluateAlert(alert: AlertState, ctx: AlertContext): EvaluationResult {
  const base = { observedValue: ctx.observation.value, deactivate: false, dedupeKey: null };
  if (!alert.isActive) return { ...base, outcome: "skipped", conditionMet: alert.lastConditionMet, reason: "Alert is disabled." };

  const isUnlock = alert.type === "unlock_upcoming";
  if (!isUnlock && (ctx.observation.value === null || !Number.isFinite(ctx.observation.value))) {
    return { ...base, outcome: "skipped", conditionMet: alert.lastConditionMet, reason: "Required market data is missing." };
  }
  if (!ctx.observation.observedAt) {
    return { ...base, outcome: "skipped", conditionMet: alert.lastConditionMet, reason: "Observation timestamp is missing." };
  }
  const ageMin = (Date.parse(ctx.now) - Date.parse(ctx.observation.observedAt)) / 60_000;
  if (!Number.isFinite(ageMin) || ageMin > ctx.maxDataAgeMinutes) {
    return { ...base, outcome: "skipped", conditionMet: alert.lastConditionMet, reason: "Market data is stale; alert not evaluated." };
  }

  const met = conditionMet(alert.type, ctx.observation.value ?? 0, alert.threshold, ctx);
  if (!met) return { ...base, outcome: "not_met", conditionMet: false, reason: "Condition not met." };
  if (alert.lastConditionMet === true) {
    return { ...base, outcome: "suppressed", conditionMet: true, reason: "Condition still met since last trigger (waiting to re-arm)." };
  }
  if (alert.lastTriggeredAt) {
    const sinceMin = (Date.parse(ctx.now) - Date.parse(alert.lastTriggeredAt)) / 60_000;
    if (sinceMin < alert.cooldownMinutes) {
      // Keep lastConditionMet=false so the alert fires once the cooldown ends if still met.
      return { ...base, outcome: "suppressed", conditionMet: false, reason: "Within cooldown period." };
    }
  }
  // Dedupe on the cooldown window so a retried job run produces the same key.
  const windowMs = alert.cooldownMinutes * 60_000;
  const bucket = Math.floor(Date.parse(ctx.now) / windowMs);
  return {
    ...base,
    outcome: "triggered",
    conditionMet: true,
    reason: "Condition met.",
    dedupeKey: `alert:${alert.id}:${bucket}`,
    deactivate: alert.triggerMode === "once",
  };
}

export function describeAlert(type: AlertType, threshold: number, coinLabel?: string | null): string {
  const label = ALERT_TYPE_LABELS[type];
  const target = coinLabel ? `${coinLabel}: ` : "";
  if (type === "price_above" || type === "price_below" || type === "portfolio_value_above" || type === "portfolio_value_below") {
    return `${target}${label} $${threshold.toLocaleString("en-US")}`;
  }
  return `${target}${label} ${threshold}`;
}
