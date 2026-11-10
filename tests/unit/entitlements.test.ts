import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { FEATURE_KEYS, FEATURE_MATRIX, PLAN_FEATURES, TIER_LIMITS } from "@/config/plans";

/**
 * The entitlement matrix, written out literally: any change to who gets what must
 * change this table on purpose.
 */
const EXPECTED: Record<string, { free: boolean; pro: boolean; premium: boolean }> = {
  "analytics.advanced": { free: false, pro: true, premium: true },
  "signals.history": { free: false, pro: true, premium: true },
  "signals.alerts": { free: false, pro: true, premium: true },
  "performance.reports": { free: false, pro: true, premium: true },
  "models.analytics": { free: false, pro: false, premium: true },
  backtesting: { free: false, pro: false, premium: true },
  "strategies.custom": { free: false, pro: false, premium: true },
};

function files(dir: string, out: string[] = []): string[] {
  for (const f of readdirSync(dir)) {
    const p = join(dir, f);
    if (statSync(p).isDirectory()) files(p, out);
    else if (/\.(ts|tsx|sql)$/.test(p)) out.push(p);
  }
  return out;
}

describe("entitlement matrix", () => {
  it("matches the expected matrix exactly (feature x tier)", () => {
    expect(Object.keys(EXPECTED).sort()).toEqual([...FEATURE_KEYS].sort());
    for (const key of FEATURE_KEYS) {
      expect({ free: PLAN_FEATURES.free[key], pro: PLAN_FEATURES.pro[key], premium: PLAN_FEATURES.premium[key] }, key).toEqual(EXPECTED[key]);
    }
  });

  it("every listed feature is actually enforced somewhere outside the plan catalog (no empty promises)", () => {
    const sources = [...files("src"), ...files("supabase/migrations")].filter(
      (f) => !f.endsWith("config/plans.ts") && !f.includes("20261110000200_nexus_tiers_features"),
    );
    const text = sources.map((f) => readFileSync(f, "utf8")).join("\n");
    for (const key of FEATURE_KEYS) expect(text.includes(`"${key}"`) || text.includes(`'${key}'`), `${key} is not enforced anywhere`).toBe(true);
  });

  it("backtest quota follows the backtesting entitlement", () => {
    for (const tier of ["free", "pro", "premium"] as const) {
      expect(TIER_LIMITS[tier].backtestsPerDay > 0, tier).toBe(PLAN_FEATURES[tier].backtesting);
    }
  });

  it("the pricing page never lists signal features (they appear only behind gate G-2)", () => {
    expect(FEATURE_MATRIX.some((f) => /signal/i.test(f.feature))).toBe(false);
  });
});
