import "server-only";
import { SCORE_SAMPLE_COIN_IDS, UNSCORED_COIN_IDS } from "@/config/coins";
import { DEFAULT_SCORING_CONFIG, type ScoringConfig } from "@/config/scoring";
import { hasTier, type Viewer } from "@/lib/auth/session";
import { logger } from "@/lib/logger";
import { getMarketService } from "@/lib/market";
import { buildScoringInput } from "./inputs";
import { computeScore, validateScoringConfig, type ScoreResult, type Subscore } from "./scoring";

/**
 * Read access to research scores.
 *
 * - With Supabase: scores are read from `research_scores` (written by the
 *   scoring job). RLS returns only sample coins to free users.
 * - Without Supabase (local development): scores are computed live from the
 *   configured provider, and the same sample-only gate is applied here.
 */

export interface StoredScore {
  coinId: string;
  methodologyVersion: string;
  status: "scored" | "insufficient_data";
  overallScore: number | null;
  previousOverallScore: number | null;
  coverage: number;
  subscores: Subscore[];
  flags: string[];
  dataAsOf: string | null;
  calculatedAt: string;
  origin: "database" | "live";
}

export type ScoreAccess = { kind: "visible"; score: StoredScore } | { kind: "locked" } | { kind: "unavailable" } | { kind: "unscored"; reason: string };

export function sampleCoinIds(source: string, universe: readonly string[]): readonly string[] {
  return source === "mock" ? universe.slice(0, 5) : SCORE_SAMPLE_COIN_IDS;
}

export async function getActiveScoringConfig(viewer: Viewer): Promise<{ config: ScoringConfig; version: number | null }> {
  if (!viewer.supabase) return { config: DEFAULT_SCORING_CONFIG, version: null };
  const { data, error } = await viewer.supabase.from("scoring_configs").select("version, config").eq("is_active", true).maybeSingle();
  if (error || !data) return { config: DEFAULT_SCORING_CONFIG, version: null };
  try {
    return { config: validateScoringConfig(data.config), version: data.version as number };
  } catch {
    logger.warn("invalid_active_scoring_config", { version: data.version });
    return { config: DEFAULT_SCORING_CONFIG, version: null };
  }
}

function fromRow(r: Record<string, unknown>): StoredScore {
  return {
    coinId: r.coin_id as string,
    methodologyVersion: r.methodology_version as string,
    status: r.status as StoredScore["status"],
    overallScore: r.overall_score === null ? null : Number(r.overall_score),
    previousOverallScore: r.previous_overall_score === null || r.previous_overall_score === undefined ? null : Number(r.previous_overall_score),
    coverage: Number(r.coverage),
    subscores: r.subscores as Subscore[],
    flags: (r.flags as string[]) ?? [],
    dataAsOf: (r.data_as_of as string | null) ?? null,
    calculatedAt: r.calculated_at as string,
    origin: "database",
  };
}

function fromResult(r: ScoreResult): StoredScore {
  return {
    coinId: r.coinId,
    methodologyVersion: r.methodologyVersion,
    status: r.status,
    overallScore: r.overallScore,
    previousOverallScore: null,
    coverage: r.coverage,
    subscores: r.subscores,
    flags: r.flags,
    dataAsOf: r.dataAsOf,
    calculatedAt: r.asOf,
    origin: "live",
  };
}

/** Scores visible to this viewer for many coins (markets table, home page). */
export async function getVisibleScores(viewer: Viewer): Promise<Map<string, StoredScore>> {
  const out = new Map<string, StoredScore>();
  if (viewer.supabase) {
    const { data, error } = await viewer.supabase.from("research_scores").select("*").limit(1000);
    if (error) {
      logger.warn("scores_read_failed", { code: error.code });
      return out;
    }
    for (const r of data ?? []) out.set(r.coin_id as string, fromRow(r));
    return out;
  }
  // Live mode: compute from the cached universe markets (no history ⇒ volatility missing).
  const svc = getMarketService();
  const markets = await svc.getUniverseMarkets().catch(() => null);
  if (!markets) return out;
  const premium = hasTier(viewer, "premium");
  const sample = new Set(sampleCoinIds(svc.source, svc.universeIds));
  for (const coin of markets.data) {
    if (UNSCORED_COIN_IDS.has(coin.id)) continue;
    if (!premium && !sample.has(coin.id)) continue;
    const input = buildScoringInput({ coin, asOf: markets.fetchedAt, observedAt: coin.lastUpdated ?? markets.fetchedAt, history: null });
    out.set(coin.id, fromResult(computeScore(input, DEFAULT_SCORING_CONFIG)));
  }
  return out;
}

export async function getScoreForCoin(viewer: Viewer, coinId: string): Promise<ScoreAccess> {
  if (UNSCORED_COIN_IDS.has(coinId)) return { kind: "unscored", reason: "Stablecoins are not scored: the methodology is designed for assets with variable prices." };
  const svc = getMarketService();
  const premium = hasTier(viewer, "premium");
  const isSample = sampleCoinIds(svc.source, svc.universeIds).includes(coinId);

  if (viewer.supabase) {
    const { data, error } = await viewer.supabase.from("research_scores").select("*").eq("coin_id", coinId).maybeSingle();
    if (error) return { kind: "unavailable" };
    if (data) return { kind: "visible", score: fromRow(data) };
    // RLS hides non-sample scores from free users; distinguish locked from not-yet-computed.
    return premium || isSample ? { kind: "unavailable" } : { kind: "locked" };
  }

  if (!premium && !isSample) return { kind: "locked" };
  const [coin, history] = await Promise.all([svc.getCoin(coinId).catch(() => null), svc.getHistory(coinId, "90d").catch(() => null)]);
  if (!coin?.data) return { kind: "unavailable" };
  const input = buildScoringInput({
    coin: coin.data,
    asOf: coin.fetchedAt,
    observedAt: coin.data.lastUpdated ?? coin.fetchedAt,
    history: history?.data ?? null,
  });
  return { kind: "visible", score: fromResult(computeScore(input, DEFAULT_SCORING_CONFIG)) };
}

export interface ScoreHistoryPoint {
  calculatedAt: string;
  overallScore: number | null;
  status: string;
  methodologyVersion: string;
}

export async function getScoreHistory(viewer: Viewer, coinId: string, limit = 60): Promise<ScoreHistoryPoint[] | null> {
  if (!viewer.supabase || !hasTier(viewer, "premium")) return null;
  const { data, error } = await viewer.supabase
    .from("research_score_snapshots")
    .select("calculated_at, overall_score, status, methodology_version")
    .eq("coin_id", coinId)
    .order("calculated_at", { ascending: false })
    .limit(limit);
  if (error) return null;
  return (data ?? []).map((r) => ({
    calculatedAt: r.calculated_at as string,
    overallScore: r.overall_score === null ? null : Number(r.overall_score),
    status: r.status as string,
    methodologyVersion: r.methodology_version as string,
  }));
}
