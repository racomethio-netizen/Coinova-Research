import "server-only";
import { DEFAULT_SCORING_CONFIG, type ScoringConfig } from "@/config/scoring";
import type { CoinDetails } from "@/lib/market/types";
import { buildScoringInput } from "@/lib/research/inputs";
import { computeScore, validateScoringConfig } from "@/lib/research/scoring";
import { estimateAnnualInflation, type UnlockEvent } from "@/lib/research/tokenomics";
import type { JobContext } from "./runner";

/**
 * compute-scores (hourly): deterministic scores from persisted inputs only
 * (latest snapshot, stored daily history, supply history, verified unlocks,
 * cached fundamentals). Snapshots are de-duplicated by inputs hash, so
 * re-running with unchanged inputs records nothing new.
 */
export async function computeScores({ db, now, log }: JobContext) {
  const cfgRow = await db.from("scoring_configs").select("version, config").eq("is_active", true).maybeSingle();
  let config: ScoringConfig = DEFAULT_SCORING_CONFIG;
  let configVersion: number | null = null;
  if (cfgRow.data) {
    try {
      config = validateScoringConfig(cfgRow.data.config);
      configVersion = cfgRow.data.version;
    } catch {
      log.warn("active_scoring_config_invalid_using_default", { version: cfgRow.data.version });
    }
  }

  const { data: coins, error } = await db
    .from("cryptocurrencies")
    .select("id, symbol, name, dev_commits_4w, dev_stars, dev_contributors, genesis_date")
    .eq("is_active", true)
    .eq("is_scored", true);
  if (error) throw new Error(error.message);
  const ids = (coins ?? []).map((c) => c.id);
  if (!ids.length) return { scored: 0, insufficient: 0 };

  const [snaps, existing, unlocks] = await Promise.all([
    db.from("latest_market_snapshots").select("*").in("coin_id", ids),
    db.from("research_scores").select("coin_id, overall_score"),
    db.from("token_unlock_events").select("id, coin_id, unlock_at, amount, allocation, source_type, source_url, confidence").eq("status", "scheduled").gte("unlock_at", now.toISOString()),
  ]);
  if (snaps.error) throw new Error(snaps.error.message);
  const snapBy = new Map((snaps.data ?? []).map((s) => [s.coin_id as string, s]));
  const prevBy = new Map((existing.data ?? []).map((s) => [s.coin_id as string, s.overall_score === null ? null : Number(s.overall_score)]));
  const unlocksBy = new Map<string, UnlockEvent[]>();
  for (const u of unlocks.data ?? []) {
    const list = unlocksBy.get(u.coin_id) ?? [];
    list.push({ id: u.id, unlockAt: u.unlock_at, amount: Number(u.amount), allocation: u.allocation, sourceType: u.source_type, sourceUrl: u.source_url, confidence: u.confidence });
    unlocksBy.set(u.coin_id, list);
  }
  const since90 = new Date(now.getTime() - 95 * 86_400_000).toISOString();
  const since400 = new Date(now.getTime() - 400 * 86_400_000).toISOString().slice(0, 10);

  let scored = 0;
  let insufficient = 0;
  for (const c of coins ?? []) {
    const s = snapBy.get(c.id);
    if (!s) continue;
    const [hist, supply] = await Promise.all([
      db.from("historical_prices").select("ts, price_usd").eq("coin_id", c.id).eq("granularity", "1d").gte("ts", since90).order("ts"),
      db.from("tokenomics_snapshots").select("snapshot_date, circulating_supply").eq("coin_id", c.id).gte("snapshot_date", since400).order("snapshot_date"),
    ]);
    const num = (v: unknown) => (v === null || v === undefined ? null : Number(v));
    const coin: CoinDetails = {
      id: c.id,
      symbol: c.symbol,
      name: c.name,
      imageUrl: null,
      marketCapRank: s.market_cap_rank,
      priceUsd: num(s.price_usd),
      marketCapUsd: num(s.market_cap_usd),
      fullyDilutedValuationUsd: num(s.fdv_usd),
      volume24hUsd: num(s.volume_24h_usd),
      high24hUsd: null,
      low24hUsd: null,
      change1hPct: num(s.change_1h_pct),
      change24hPct: num(s.change_24h_pct),
      change7dPct: num(s.change_7d_pct),
      change30dPct: num(s.change_30d_pct),
      circulatingSupply: num(s.circulating_supply),
      totalSupply: num(s.total_supply),
      maxSupply: num(s.max_supply),
      athUsd: null,
      athChangePct: null,
      lastUpdated: s.provider_updated_at,
      description: null,
      categories: [],
      homepageUrl: null,
      whitepaperUrl: null,
      githubRepos: [],
      genesisDate: c.genesis_date,
      hashingAlgorithm: null,
      developer:
        c.dev_commits_4w === null && c.dev_stars === null && c.dev_contributors === null
          ? null
          : { commits4Weeks: c.dev_commits_4w, stars: c.dev_stars, forks: null, contributors: c.dev_contributors },
    };
    const inflation = estimateAnnualInflation((supply.data ?? []).filter((r) => r.circulating_supply !== null).map((r) => ({ at: r.snapshot_date, circulatingSupply: Number(r.circulating_supply) })));
    const input = buildScoringInput({
      coin,
      asOf: now.toISOString(),
      observedAt: s.captured_at,
      history: (hist.data ?? []).map((h) => ({ t: Date.parse(h.ts), priceUsd: Number(h.price_usd), marketCapUsd: null, volumeUsd: null })),
      estimatedAnnualInflationPct: inflation,
      unlockEvents: unlocksBy.get(c.id) ?? null,
    });
    const result = computeScore(input, config);
    if (result.status === "scored") scored++;
    else insufficient++;

    const row = {
      coin_id: c.id,
      methodology_version: result.methodologyVersion,
      config_version: configVersion,
      status: result.status,
      overall_score: result.overallScore,
      coverage: result.coverage,
      subscores: result.subscores,
      flags: result.flags,
      inputs_hash: result.inputsHash,
      data_as_of: result.dataAsOf,
      calculated_at: result.asOf,
    };
    const up = await db.from("research_scores").upsert({ ...row, previous_overall_score: prevBy.get(c.id) ?? null }, { onConflict: "coin_id" });
    if (up.error) throw new Error(`score upsert failed for ${c.id}: ${up.error.message}`);
    await db.from("research_score_snapshots").upsert({ ...row, inputs: input }, { onConflict: "coin_id,methodology_version,inputs_hash", ignoreDuplicates: true });
  }
  return { scored, insufficient, configVersion };
}
