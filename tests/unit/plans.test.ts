import { describe, expect, it } from "vitest";
import { FEATURE_KEYS, getPaidPlan, minimumTierFor, normalizeTier, PAID_PLANS, PLAN_FEATURES, TIER_LIMITS, TIER_RANK, tierHasFeature, type MembershipTier } from "@/config/plans";
import { getNexusGates, parseEnv } from "@/lib/env";

const TIERS = Object.keys(TIER_RANK) as MembershipTier[];

describe("membership tiers", () => {
  it("are ordered free < pro < premium", () => {
    expect(TIER_RANK.free).toBeLessThan(TIER_RANK.pro);
    expect(TIER_RANK.pro).toBeLessThan(TIER_RANK.premium);
  });

  it("normalize legacy and unknown values safely", () => {
    expect(normalizeTier("founding")).toBe("premium");
    expect(normalizeTier("pro")).toBe("pro");
    expect(normalizeTier("admin")).toBe("free");
    expect(normalizeTier(null)).toBe("free");
    expect(normalizeTier("__proto__")).toBe("free");
  });

  it("higher tiers never have lower limits", () => {
    for (const key of Object.keys(TIER_LIMITS.free) as (keyof typeof TIER_LIMITS.free)[]) {
      expect(TIER_LIMITS.pro[key]).toBeGreaterThanOrEqual(TIER_LIMITS.free[key]);
      expect(TIER_LIMITS.premium[key]).toBeGreaterThanOrEqual(TIER_LIMITS.pro[key]);
    }
  });

  it("the founding plan is no longer purchasable", () => {
    expect(getPaidPlan("founding_yearly")).toBeUndefined();
    expect(PAID_PLANS.map((p) => p.tier).sort()).toEqual(["premium", "premium", "pro", "pro"]);
  });
});

describe("feature entitlements", () => {
  it("are monotonic: a higher tier has every feature of a lower tier", () => {
    for (const f of FEATURE_KEYS) {
      for (const t of TIERS) {
        expect(tierHasFeature(t, f)).toBe(TIER_RANK[t] >= TIER_RANK[minimumTierFor(f)]);
      }
    }
  });

  it("match the NEXUS plan definitions", () => {
    expect(PLAN_FEATURES.free["charts.basic"]).toBe(true);
    expect(PLAN_FEATURES.free["signals.history"]).toBe(false);
    expect(PLAN_FEATURES.pro["signals.alerts"]).toBe(true);
    expect(PLAN_FEATURES.pro["performance.reports"]).toBe(true);
    expect(PLAN_FEATURES.pro.backtesting).toBe(false);
    expect(PLAN_FEATURES.premium.backtesting).toBe(true);
    expect(PLAN_FEATURES.premium["strategies.custom"]).toBe(true);
    expect(PLAN_FEATURES.premium["models.analytics"]).toBe(true);
  });
});

describe("compliance gates", () => {
  it("default to the safest values", () => {
    expect(getNexusGates(parseEnv({}))).toEqual({ signalsVisibility: "staff", paidSignalsEnabled: false, liveExecutionEnabled: false });
  });

  it("refuse to enable live execution", () => {
    expect(() => parseEnv({ LIVE_EXECUTION_ENABLED: "true" })).toThrow(/LIVE_EXECUTION_ENABLED/);
  });

  it("require public visibility for paid signals", () => {
    expect(() => parseEnv({ PAID_SIGNALS_ENABLED: "true" })).toThrow(/PAID_SIGNALS_ENABLED/);
    expect(getNexusGates(parseEnv({ PAID_SIGNALS_ENABLED: "true", SIGNALS_VISIBILITY: "public" })).paidSignalsEnabled).toBe(true);
  });

  it("reject ambiguous flag values", () => {
    expect(() => parseEnv({ LIVE_EXECUTION_ENABLED: "yes" })).toThrow(/LIVE_EXECUTION_ENABLED/);
    expect(() => parseEnv({ SIGNALS_VISIBILITY: "everyone" })).toThrow(/SIGNALS_VISIBILITY/);
  });
});
