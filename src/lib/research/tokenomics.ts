/**
 * Supply and token-unlock analytics.
 *
 * Every derived value carries its provenance so the UI can distinguish
 * provider-verified data, project-reported data, estimates, and gaps.
 */

export type Provenance = "provider" | "project_reported" | "estimated" | "missing";

export interface Sourced<T> {
  value: T | null;
  provenance: Provenance;
  note?: string;
}

export interface SupplyInput {
  circulatingSupply: number | null;
  totalSupply: number | null;
  maxSupply: number | null;
  marketCapUsd: number | null;
  fullyDilutedValuationUsd: number | null;
  priceUsd: number | null;
}

export interface SupplyPoint {
  /** ISO date or datetime. */
  at: string;
  circulatingSupply: number;
}

export interface UnlockEvent {
  id: string;
  unlockAt: string;
  amount: number;
  allocation: string | null;
  sourceType: "provider" | "project_reported" | "estimated";
  sourceUrl: string | null;
  confidence: "high" | "medium" | "low";
}

export interface SupplyMetrics {
  circulatingSupply: Sourced<number>;
  totalSupply: Sourced<number>;
  maxSupply: Sourced<number>;
  hasFixedMaxSupply: boolean;
  circulatingPctOfMax: Sourced<number>;
  circulatingPctOfTotal: Sourced<number>;
  marketCapUsd: Sourced<number>;
  fullyDilutedValuationUsd: Sourced<number>;
  fdvToMarketCap: Sourced<number>;
  remainingToMaxSupply: Sourced<number>;
  estimatedAnnualInflationPct: Sourced<number>;
}

const pos = (x: number | null | undefined): x is number => typeof x === "number" && Number.isFinite(x) && x > 0;
const provider = (v: number | null): Sourced<number> => (pos(v) ? { value: v, provenance: "provider" } : { value: null, provenance: "missing" });
const estimated = (v: number | null, note: string): Sourced<number> => (v !== null && Number.isFinite(v) ? { value: v, provenance: "estimated", note } : { value: null, provenance: "missing" });

/**
 * Annualized circulating-supply growth from a series of supply observations.
 * Uses the earliest and latest points; requires at least `minDays` between them.
 */
export function estimateAnnualInflation(points: readonly SupplyPoint[], minDays = 30): number | null {
  const valid = points
    .filter((p) => pos(p.circulatingSupply) && Number.isFinite(Date.parse(p.at)))
    .sort((a, b) => Date.parse(a.at) - Date.parse(b.at));
  if (valid.length < 2) return null;
  const first = valid[0]!;
  const last = valid[valid.length - 1]!;
  const days = (Date.parse(last.at) - Date.parse(first.at)) / 86_400_000;
  if (days < minDays) return null;
  const growth = last.circulatingSupply / first.circulatingSupply;
  return (Math.pow(growth, 365 / days) - 1) * 100;
}

export function computeSupplyMetrics(input: SupplyInput, history: readonly SupplyPoint[] = []): SupplyMetrics {
  const { circulatingSupply: c, totalSupply: t, maxSupply: m, marketCapUsd: mc, priceUsd: p } = input;
  // FDV: prefer the provider's value; otherwise estimate price × max supply when a max exists.
  let fdv: Sourced<number> = provider(input.fullyDilutedValuationUsd);
  if (fdv.provenance === "missing" && pos(p) && pos(m)) fdv = estimated(p * m, "Price × maximum supply.");
  const fdvToMc =
    pos(fdv.value) && pos(mc)
      ? ({ value: fdv.value / mc, provenance: fdv.provenance === "provider" ? "provider" : "estimated" } as Sourced<number>)
      : ({ value: null, provenance: "missing" } as Sourced<number>);
  const infl = estimateAnnualInflation(history);
  return {
    circulatingSupply: provider(c),
    totalSupply: provider(t),
    maxSupply: pos(m) ? { value: m, provenance: "provider" } : { value: null, provenance: "missing", note: "No fixed maximum supply reported. The asset may have uncapped issuance." },
    hasFixedMaxSupply: pos(m),
    circulatingPctOfMax: pos(c) && pos(m) ? { value: (c / m) * 100, provenance: "provider" } : { value: null, provenance: "missing" },
    circulatingPctOfTotal: pos(c) && pos(t) ? { value: (c / t) * 100, provenance: "provider" } : { value: null, provenance: "missing" },
    marketCapUsd: provider(mc),
    fullyDilutedValuationUsd: fdv,
    fdvToMarketCap: fdvToMc,
    remainingToMaxSupply: pos(c) && pos(m) ? { value: Math.max(0, m - c), provenance: "provider" } : { value: null, provenance: "missing" },
    estimatedAnnualInflationPct:
      infl === null
        ? { value: null, provenance: "missing", note: "Requires at least 30 days of recorded supply history." }
        : { value: infl, provenance: "estimated", note: "Annualized from recorded circulating-supply snapshots." },
  };
}

export interface UnlockSummary {
  windowDays: number;
  events: (UnlockEvent & { pctOfCirculating: number | null; valueUsd: number | null; pctOfMarketCap: number | null })[];
  totalAmount: number;
  totalPctOfCirculating: number | null;
  totalPctOfMarketCap: number | null;
  /** True only if every event in the window is provider or project reported. */
  allVerified: boolean;
}

export function summarizeUnlocks(
  events: readonly UnlockEvent[],
  supply: Pick<SupplyInput, "circulatingSupply" | "priceUsd" | "marketCapUsd">,
  asOf: string,
  windowDays = 90,
): UnlockSummary {
  const now = Date.parse(asOf);
  const end = now + windowDays * 86_400_000;
  const inWindow = events
    .filter((e) => {
      const t = Date.parse(e.unlockAt);
      return Number.isFinite(t) && t >= now && t <= end && pos(e.amount);
    })
    .sort((a, b) => Date.parse(a.unlockAt) - Date.parse(b.unlockAt));
  const c = supply.circulatingSupply;
  const price = supply.priceUsd;
  const mc = supply.marketCapUsd;
  const enriched = inWindow.map((e) => {
    const valueUsd = pos(price) ? e.amount * price : null;
    return {
      ...e,
      pctOfCirculating: pos(c) ? (e.amount / c) * 100 : null,
      valueUsd,
      pctOfMarketCap: valueUsd !== null && pos(mc) ? (valueUsd / mc) * 100 : null,
    };
  });
  const totalAmount = inWindow.reduce((s, e) => s + e.amount, 0);
  const totalValue = pos(price) ? totalAmount * price : null;
  return {
    windowDays,
    events: enriched,
    totalAmount,
    totalPctOfCirculating: pos(c) ? (totalAmount / c) * 100 : null,
    totalPctOfMarketCap: totalValue !== null && pos(mc) ? (totalValue / mc) * 100 : null,
    allVerified: inWindow.every((e) => e.sourceType !== "estimated"),
  };
}
