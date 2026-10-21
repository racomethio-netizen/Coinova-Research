import { describe, expect, it } from "vitest";
import { DEFAULT_SCORING_CONFIG, type ScoringConfig } from "@/config/scoring";
import { computeScore, dailyVolatility, InvalidScoringInputError, scoreBand, validateScoringConfig, type ScoringInput } from "@/lib/research/scoring";

const ASOF = "2026-10-01T12:00:00.000Z";

function closes(n: number, drift = 0.001, amp = 0.02): number[] {
  const out = [100];
  for (let i = 1; i < n; i++) out.push(out[i - 1]! * (1 + drift + (i % 2 === 0 ? amp : -amp)));
  return out;
}

function baseInput(overrides: Partial<ScoringInput> = {}): ScoringInput {
  return {
    coinId: "testcoin",
    asOf: ASOF,
    market: {
      observedAt: "2026-10-01T11:55:00.000Z",
      priceUsd: 10,
      marketCapUsd: 1_000_000_000,
      fullyDilutedValuationUsd: 1_250_000_000,
      volume24hUsd: 80_000_000,
      change7dPct: 5,
      change30dPct: 12,
      circulatingSupply: 100_000_000,
      totalSupply: 120_000_000,
      maxSupply: 125_000_000,
    },
    fundamentals: { commits4Weeks: 150, githubStars: 5000, contributors: 40, ageYears: 4 },
    dailyCloses: closes(60),
    estimatedAnnualInflationPct: 3,
    unlocks: { next90dPctOfCirculating: 1.5, verified: true },
    ...overrides,
  };
}

describe("computeScore", () => {
  it("produces a full score when all inputs are present", () => {
    const r = computeScore(baseInput(), DEFAULT_SCORING_CONFIG);
    expect(r.status).toBe("scored");
    expect(r.coverage).toBe(1);
    expect(r.overallScore).not.toBeNull();
    expect(r.overallScore!).toBeGreaterThanOrEqual(0);
    expect(r.overallScore!).toBeLessThanOrEqual(100);
    expect(r.subscores).toHaveLength(6);
    expect(r.subscores.every((s) => s.status === "ok")).toBe(true);
    expect(r.methodologyVersion).toBe(DEFAULT_SCORING_CONFIG.methodologyVersion);
  });

  it("overall score equals the weighted average of subscores", () => {
    const r = computeScore(baseInput(), DEFAULT_SCORING_CONFIG);
    const expected = r.subscores.reduce((s, x) => s + x.score! * x.weight, 0);
    expect(r.overallScore).toBeCloseTo(expected, 0);
  });

  it("is deterministic: same input and config produce identical results and hash", () => {
    const a = computeScore(baseInput(), DEFAULT_SCORING_CONFIG);
    const b = computeScore(baseInput(), DEFAULT_SCORING_CONFIG);
    expect(a).toEqual(b);
    expect(a.inputsHash).toMatch(/^[a-f0-9]{64}$/);
  });

  it("hash changes with methodology version even when inputs are identical", () => {
    const a = computeScore(baseInput(), DEFAULT_SCORING_CONFIG);
    const b = computeScore(baseInput(), { ...DEFAULT_SCORING_CONFIG, methodologyVersion: "1.1.0" });
    expect(b.methodologyVersion).toBe("1.1.0");
    expect(a.inputsHash).not.toBe(b.inputsHash);
    expect(a.overallScore).toBe(b.overallScore);
  });

  it("does not treat missing unlock data as low quality; renormalizes and reports coverage", () => {
    const r = computeScore(baseInput({ unlocks: null }), DEFAULT_SCORING_CONFIG);
    const unlock = r.subscores.find((s) => s.category === "unlockPressure")!;
    expect(unlock.status).toBe("missing");
    expect(unlock.score).toBeNull();
    expect(r.status).toBe("scored");
    expect(r.coverage).toBeCloseTo(0.9);
    expect(r.flags).toContain("unlockPressure_missing");
  });

  it("missing data never lowers the score relative to the remaining categories", () => {
    const full = computeScore(baseInput(), DEFAULT_SCORING_CONFIG);
    const noFund = computeScore(baseInput({ fundamentals: null }), DEFAULT_SCORING_CONFIG);
    const others = full.subscores.filter((s) => s.category !== "fundamentals");
    const w = others.reduce((s, x) => s + x.weight, 0);
    expect(noFund.overallScore).toBeCloseTo(others.reduce((s, x) => s + x.score! * x.weight, 0) / w, 0);
  });

  it("returns insufficient_data when coverage is below the threshold", () => {
    const r = computeScore(
      baseInput({ fundamentals: null, unlocks: null, dailyCloses: null, market: { ...baseInput().market, change7dPct: null, change30dPct: null } }),
      DEFAULT_SCORING_CONFIG,
    );
    expect(r.coverage).toBeCloseTo(0.45);
    expect(r.status).toBe("insufficient_data");
    expect(r.overallScore).toBeNull();
  });

  it("returns insufficient_data when a required category is missing even with enough coverage", () => {
    const m = { ...baseInput().market, volume24hUsd: null };
    const r = computeScore(baseInput({ market: m }), DEFAULT_SCORING_CONFIG);
    expect(r.coverage).toBeCloseTo(0.8);
    expect(r.status).toBe("insufficient_data");
    expect(r.flags).toContain("required_missing:liquidity");
  });

  it("excludes market-dependent categories when market data is stale", () => {
    const m = { ...baseInput().market, observedAt: "2026-09-30T00:00:00.000Z" };
    const r = computeScore(baseInput({ market: m }), DEFAULT_SCORING_CONFIG);
    expect(r.flags).toContain("market_data_stale");
    expect(r.subscores.find((s) => s.category === "liquidity")!.status).toBe("unreliable");
    expect(r.status).toBe("insufficient_data");
  });

  it("flags missing market timestamp as unreliable", () => {
    const m = { ...baseInput().market, observedAt: null };
    const r = computeScore(baseInput({ market: m }), DEFAULT_SCORING_CONFIG);
    expect(r.flags).toContain("market_data_timestamp_missing");
    expect(r.status).toBe("insufficient_data");
  });

  it("flags negative values as invalid input", () => {
    const m = { ...baseInput().market, marketCapUsd: -5 };
    const r = computeScore(baseInput({ market: m }), DEFAULT_SCORING_CONFIG);
    expect(r.flags.some((f) => f.startsWith("negative_values"))).toBe(true);
    expect(r.status).toBe("insufficient_data");
  });

  it("rejects structurally invalid input", () => {
    expect(() => computeScore({ coinId: "x" }, DEFAULT_SCORING_CONFIG)).toThrow(InvalidScoringInputError);
    expect(() => computeScore({ ...baseInput(), asOf: "not-a-date" }, DEFAULT_SCORING_CONFIG)).toThrow(InvalidScoringInputError);
  });

  it("treats unverified unlock data as unreliable, not as zero", () => {
    const r = computeScore(baseInput({ unlocks: { next90dPctOfCirculating: 50, verified: false } }), DEFAULT_SCORING_CONFIG);
    const u = r.subscores.find((s) => s.category === "unlockPressure")!;
    expect(u.status).toBe("unreliable");
    expect(u.score).toBeNull();
  });

  it("keeps every subscore within 0–100 at extreme values", () => {
    const extremes: Partial<ScoringInput>[] = [
      { market: { ...baseInput().market, change7dPct: 10_000, change30dPct: 1e6, volume24hUsd: 1e15 } },
      { market: { ...baseInput().market, change7dPct: -99.9, change30dPct: -99.99, volume24hUsd: 1 } },
      { estimatedAnnualInflationPct: 5000 },
      { estimatedAnnualInflationPct: -50 },
      { unlocks: { next90dPctOfCirculating: 900, verified: true } },
      { dailyCloses: closes(60, 0, 0.5) },
      { fundamentals: { commits4Weeks: 1e9, githubStars: 1e9, contributors: 1e9, ageYears: 100 } },
      { market: { ...baseInput().market, fullyDilutedValuationUsd: 1e18 } },
    ];
    for (const e of extremes) {
      const r = computeScore(baseInput(e), DEFAULT_SCORING_CONFIG);
      for (const s of r.subscores) {
        if (s.score !== null) {
          expect(s.score).toBeGreaterThanOrEqual(0);
          expect(s.score).toBeLessThanOrEqual(100);
        }
      }
      if (r.overallScore !== null) {
        expect(r.overallScore).toBeGreaterThanOrEqual(0);
        expect(r.overallScore).toBeLessThanOrEqual(100);
      }
    }
  });

  it("boundary: momentum is neutral (50) at zero change", () => {
    const r = computeScore(baseInput({ market: { ...baseInput().market, change7dPct: 0, change30dPct: 0 } }), DEFAULT_SCORING_CONFIG);
    expect(r.subscores.find((s) => s.category === "momentum")!.score).toBe(50);
  });

  it("boundary: zero unlocks scores 100; ≥10% scores 0", () => {
    const zero = computeScore(baseInput({ unlocks: { next90dPctOfCirculating: 0, verified: true } }), DEFAULT_SCORING_CONFIG);
    const ten = computeScore(baseInput({ unlocks: { next90dPctOfCirculating: 10, verified: true } }), DEFAULT_SCORING_CONFIG);
    expect(zero.subscores.find((s) => s.category === "unlockPressure")!.score).toBe(100);
    expect(ten.subscores.find((s) => s.category === "unlockPressure")!.score).toBe(0);
  });

  it("handles uncapped supply (no max supply) without assuming one", () => {
    const m = { ...baseInput().market, maxSupply: null };
    const r = computeScore(baseInput({ market: m }), DEFAULT_SCORING_CONFIG);
    const t = r.subscores.find((s) => s.category === "tokenomics")!;
    expect(t.status).toBe("ok");
    expect(t.explanation).toMatch(/No fixed maximum supply/);
  });

  it("requires a minimum history length for volatility", () => {
    const r = computeScore(baseInput({ dailyCloses: closes(5) }), DEFAULT_SCORING_CONFIG);
    expect(r.subscores.find((s) => s.category === "riskVolatility")!.status).toBe("missing");
  });

  it("respects custom weights (configurable)", () => {
    const onlyLiquidity: ScoringConfig = {
      ...DEFAULT_SCORING_CONFIG,
      weights: { tokenomics: 0, liquidity: 1, fundamentals: 0, momentum: 0, unlockPressure: 0, riskVolatility: 0 },
      requiredCategories: ["liquidity"],
    };
    const r = computeScore(baseInput(), onlyLiquidity);
    expect(r.overallScore).toBe(r.subscores.find((s) => s.category === "liquidity")!.score);
  });
});

describe("validateScoringConfig", () => {
  it("rejects weights that do not sum to 1", () => {
    expect(() => validateScoringConfig({ ...DEFAULT_SCORING_CONFIG, weights: { ...DEFAULT_SCORING_CONFIG.weights, tokenomics: 0.5 } })).toThrow();
  });
  it("rejects negative weights and invalid versions", () => {
    expect(() => validateScoringConfig({ ...DEFAULT_SCORING_CONFIG, weights: { ...DEFAULT_SCORING_CONFIG.weights, tokenomics: -0.25, liquidity: 0.7 } })).toThrow();
    expect(() => validateScoringConfig({ ...DEFAULT_SCORING_CONFIG, methodologyVersion: "v1" })).toThrow();
  });
  it("accepts the default config", () => {
    expect(validateScoringConfig(DEFAULT_SCORING_CONFIG)).toEqual(DEFAULT_SCORING_CONFIG);
  });
});

describe("helpers", () => {
  it("dailyVolatility returns null for too-short series and 0 for constant prices", () => {
    expect(dailyVolatility([1])).toBeNull();
    expect(dailyVolatility([5, 5, 5, 5])).toBe(0);
  });
  it("scoreBand labels", () => {
    expect(scoreBand(null).label).toBe("Insufficient data");
    expect(scoreBand(80).tone).toBe("strong");
    expect(scoreBand(50).tone).toBe("moderate");
    expect(scoreBand(10).tone).toBe("weak");
  });
});
