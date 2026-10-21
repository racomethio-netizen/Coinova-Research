import { describe, expect, it } from "vitest";
import { computeSupplyMetrics, estimateAnnualInflation, summarizeUnlocks, type UnlockEvent } from "@/lib/research/tokenomics";

describe("computeSupplyMetrics", () => {
  it("computes ratios with provider provenance", () => {
    const m = computeSupplyMetrics({ circulatingSupply: 50, totalSupply: 80, maxSupply: 100, marketCapUsd: 500, fullyDilutedValuationUsd: 1000, priceUsd: 10 });
    expect(m.circulatingPctOfMax).toEqual({ value: 50, provenance: "provider" });
    expect(m.circulatingPctOfTotal.value).toBeCloseTo(62.5);
    expect(m.fdvToMarketCap).toEqual({ value: 2, provenance: "provider" });
    expect(m.remainingToMaxSupply.value).toBe(50);
    expect(m.hasFixedMaxSupply).toBe(true);
  });

  it("does not assume a max supply when none is reported", () => {
    const m = computeSupplyMetrics({ circulatingSupply: 50, totalSupply: 80, maxSupply: null, marketCapUsd: 500, fullyDilutedValuationUsd: null, priceUsd: 10 });
    expect(m.hasFixedMaxSupply).toBe(false);
    expect(m.maxSupply.provenance).toBe("missing");
    expect(m.circulatingPctOfMax.provenance).toBe("missing");
    expect(m.fullyDilutedValuationUsd.provenance).toBe("missing");
  });

  it("marks FDV as estimated when derived from price × max supply", () => {
    const m = computeSupplyMetrics({ circulatingSupply: 50, totalSupply: 80, maxSupply: 100, marketCapUsd: 500, fullyDilutedValuationUsd: null, priceUsd: 10 });
    expect(m.fullyDilutedValuationUsd).toMatchObject({ value: 1000, provenance: "estimated" });
    expect(m.fdvToMarketCap.provenance).toBe("estimated");
  });

  it("treats zero/negative values as missing", () => {
    const m = computeSupplyMetrics({ circulatingSupply: 0, totalSupply: -1, maxSupply: null, marketCapUsd: null, fullyDilutedValuationUsd: null, priceUsd: null });
    expect(m.circulatingSupply.provenance).toBe("missing");
    expect(m.totalSupply.provenance).toBe("missing");
  });
});

describe("estimateAnnualInflation", () => {
  it("annualizes supply growth", () => {
    const r = estimateAnnualInflation([
      { at: "2025-01-01", circulatingSupply: 100 },
      { at: "2026-01-01", circulatingSupply: 110 },
    ]);
    expect(r).toBeCloseTo(10, 1);
  });
  it("returns null with insufficient span or points", () => {
    expect(estimateAnnualInflation([{ at: "2025-01-01", circulatingSupply: 100 }])).toBeNull();
    expect(estimateAnnualInflation([{ at: "2025-01-01", circulatingSupply: 100 }, { at: "2025-01-10", circulatingSupply: 101 }])).toBeNull();
  });
});

describe("summarizeUnlocks", () => {
  const ev = (id: string, at: string, amount: number, sourceType: UnlockEvent["sourceType"] = "project_reported"): UnlockEvent => ({
    id, unlockAt: at, amount, allocation: "team", sourceType, sourceUrl: "https://example.com", confidence: "medium",
  });

  it("includes only future events in the window and computes relative sizes", () => {
    const s = summarizeUnlocks(
      [ev("past", "2026-09-01T00:00:00Z", 10), ev("a", "2026-10-15T00:00:00Z", 10), ev("b", "2026-12-01T00:00:00Z", 30), ev("far", "2027-06-01T00:00:00Z", 99)],
      { circulatingSupply: 1000, priceUsd: 2, marketCapUsd: 2000 },
      "2026-10-01T00:00:00Z",
      90,
    );
    expect(s.events.map((e) => e.id)).toEqual(["a", "b"]);
    expect(s.totalAmount).toBe(40);
    expect(s.totalPctOfCirculating).toBeCloseTo(4);
    expect(s.totalPctOfMarketCap).toBeCloseTo(4);
    expect(s.allVerified).toBe(true);
  });

  it("flags estimated events as unverified", () => {
    const s = summarizeUnlocks([ev("a", "2026-10-15T00:00:00Z", 10, "estimated")], { circulatingSupply: 1000, priceUsd: null, marketCapUsd: null }, "2026-10-01T00:00:00Z");
    expect(s.allVerified).toBe(false);
    expect(s.events[0]!.valueUsd).toBeNull();
  });
});
