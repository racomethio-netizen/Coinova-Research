import type { CoinDetails, CoinMarket, PricePoint } from "@/lib/market/types";
import type { ScoringInput } from "./scoring";
import { summarizeUnlocks, type UnlockEvent } from "./tokenomics";

/**
 * Assemble a ScoringInput from normalized provider data. Pure; used both by
 * the scheduled scoring job and by live (no-database) development mode, so
 * both paths score identically.
 */
export function buildScoringInput(params: {
  coin: CoinMarket | CoinDetails;
  asOf: string;
  observedAt: string | null;
  history: readonly PricePoint[] | null;
  estimatedAnnualInflationPct?: number | null;
  unlockEvents?: readonly UnlockEvent[] | null;
}): ScoringInput {
  const { coin } = params;
  const details = "developer" in coin ? coin : null;
  const genesis = details?.genesisDate ? Date.parse(details.genesisDate) : NaN;
  const ageYears = Number.isFinite(genesis) ? Math.max(0, (Date.parse(params.asOf) - genesis) / (365.25 * 86_400_000)) : null;

  let unlocks: ScoringInput["unlocks"] = null;
  if (params.unlockEvents && params.unlockEvents.length > 0) {
    const s = summarizeUnlocks(params.unlockEvents, coin, params.asOf, 90);
    unlocks = s.totalPctOfCirculating === null ? null : { next90dPctOfCirculating: s.totalPctOfCirculating, verified: s.allVerified };
  } else if (params.unlockEvents && params.unlockEvents.length === 0) {
    // An empty list only means "no recorded events"; it is NOT verified evidence of no unlocks.
    unlocks = null;
  }

  return {
    coinId: coin.id,
    asOf: toUtcIso(params.asOf) ?? params.asOf,
    market: {
      observedAt: toUtcIso(params.observedAt),
      priceUsd: coin.priceUsd,
      marketCapUsd: coin.marketCapUsd,
      fullyDilutedValuationUsd: coin.fullyDilutedValuationUsd,
      volume24hUsd: coin.volume24hUsd,
      change7dPct: coin.change7dPct,
      change30dPct: coin.change30dPct,
      circulatingSupply: coin.circulatingSupply,
      totalSupply: coin.totalSupply,
      maxSupply: coin.maxSupply,
    },
    fundamentals: details?.developer
      ? { commits4Weeks: details.developer.commits4Weeks, githubStars: details.developer.stars, contributors: details.developer.contributors, ageYears }
      : ageYears !== null
        ? { commits4Weeks: null, githubStars: null, contributors: null, ageYears }
        : null,
    dailyCloses: params.history ? dailyCloses(params.history) : null,
    estimatedAnnualInflationPct: params.estimatedAnnualInflationPct ?? null,
    unlocks,
  };
}

/** Last price of each UTC day, oldest first. */
export function dailyCloses(points: readonly PricePoint[]): number[] {
  const byDay = new Map<number, number>();
  for (const p of [...points].sort((a, b) => a.t - b.t)) byDay.set(Math.floor(p.t / 86_400_000), p.priceUsd);
  return [...byDay.entries()].sort((a, b) => a[0] - b[0]).map(([, v]) => v);
}

/** Normalize any parseable timestamp (e.g. Postgres "+00:00" form) to canonical UTC ISO so input hashes are stable. */
export function toUtcIso(s: string | null): string | null {
  if (!s) return null;
  const t = Date.parse(s);
  return Number.isFinite(t) ? new Date(t).toISOString() : null;
}
