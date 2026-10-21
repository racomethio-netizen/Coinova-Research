import "server-only";
import { SCORE_SAMPLE_COIN_IDS, UNSCORED_COIN_IDS } from "@/config/coins";
import { getEnv } from "@/lib/env";
import { getMarketService } from "@/lib/market";
import { MarketDataError } from "@/lib/market/types";
import { sampleCoinIds } from "@/lib/research/service";
import { sleep, type JobContext } from "./runner";

/**
 * refresh-markets (every 5 minutes): one bulk provider call (batched by 250)
 * → upsert coin metadata, insert a snapshot per coin for this 5-minute bucket,
 * and record one supply observation per coin per day.
 */
export async function refreshMarkets({ db, now, log }: JobContext) {
  const svc = getMarketService();
  const rows = await svc.provider.getMarketData(svc.universeIds);
  const returned = new Set(rows.map((r) => r.id));
  const missing = svc.universeIds.filter((id) => !returned.has(id));
  if (missing.length) log.warn("universe_ids_not_returned_by_provider", { count: missing.length, ids: missing.slice(0, 20) });

  const bucket = new Date(Math.floor(now.getTime() / 300_000) * 300_000).toISOString();
  const sample = new Set(svc.source === "mock" ? sampleCoinIds("mock", svc.universeIds) : SCORE_SAMPLE_COIN_IDS);
  const source = svc.source;

  // Only provider-owned columns are written; staff-edited fields are left alone.
  const coins = rows.map((r) => ({
    id: r.id,
    symbol: r.symbol.slice(0, 20),
    name: r.name.slice(0, 120),
    image_url: r.imageUrl,
    market_cap_rank: r.marketCapRank,
    is_score_sample: sample.has(r.id),
    is_scored: !UNSCORED_COIN_IDS.has(r.id),
    metadata_source: source,
  }));
  const up = await db.from("cryptocurrencies").upsert(coins, { onConflict: "id" });
  if (up.error) throw new Error(`coin upsert failed: ${up.error.message}`);

  const snapshots = rows.map((r) => ({
    coin_id: r.id,
    source,
    captured_at: bucket,
    provider_updated_at: r.lastUpdated,
    price_usd: r.priceUsd,
    market_cap_usd: r.marketCapUsd,
    fdv_usd: r.fullyDilutedValuationUsd,
    volume_24h_usd: r.volume24hUsd,
    change_1h_pct: r.change1hPct,
    change_24h_pct: r.change24hPct,
    change_7d_pct: r.change7dPct,
    change_30d_pct: r.change30dPct,
    circulating_supply: r.circulatingSupply,
    total_supply: r.totalSupply,
    max_supply: r.maxSupply,
    market_cap_rank: r.marketCapRank,
  }));
  const ins = await db.from("market_snapshots").upsert(snapshots, { onConflict: "coin_id,source,captured_at", ignoreDuplicates: true });
  if (ins.error) throw new Error(`snapshot insert failed: ${ins.error.message}`);

  const day = now.toISOString().slice(0, 10);
  const supply = rows
    .filter((r) => r.circulatingSupply !== null)
    .map((r) => ({ coin_id: r.id, source, snapshot_date: day, circulating_supply: r.circulatingSupply, total_supply: r.totalSupply, max_supply: r.maxSupply }));
  const sup = await db.from("tokenomics_snapshots").upsert(supply, { onConflict: "coin_id,source,snapshot_date", ignoreDuplicates: true });
  if (sup.error) throw new Error(`supply insert failed: ${sup.error.message}`);

  return { coins: rows.length, missing: missing.length, bucket };
}

/**
 * refresh-history (daily, rotating): fetch 365 days of daily prices for the
 * coins whose history is oldest, with a delay between calls to respect
 * provider rate limits.
 */
export async function refreshHistory({ db, log }: JobContext, batch = 25) {
  const svc = getMarketService();
  const delay = getEnv().HISTORY_REFRESH_DELAY_MS;
  const { data: coins, error } = await db
    .from("cryptocurrencies")
    .select("id")
    .eq("is_active", true)
    .order("history_updated_at", { ascending: true, nullsFirst: true })
    .limit(batch);
  if (error) throw new Error(error.message);
  let updated = 0;
  let failed = 0;
  for (const { id } of coins ?? []) {
    if (!svc.isSupported(id)) continue;
    try {
      const points = await svc.provider.getHistoricalPrices(id, "365d");
      const byDay = new Map<string, (typeof points)[number]>();
      for (const p of points) byDay.set(new Date(p.t).toISOString().slice(0, 10), p);
      const rows = [...byDay.entries()].map(([d, p]) => ({
        coin_id: id,
        source: svc.source,
        granularity: "1d",
        ts: `${d}T00:00:00.000Z`,
        price_usd: p.priceUsd,
        market_cap_usd: p.marketCapUsd,
        volume_usd: p.volumeUsd,
      }));
      if (rows.length) {
        const r = await db.from("historical_prices").upsert(rows, { onConflict: "coin_id,source,granularity,ts" });
        if (r.error) throw new Error(r.error.message);
      }
      await db.from("cryptocurrencies").update({ history_updated_at: new Date().toISOString() }).eq("id", id);
      updated++;
    } catch (err) {
      failed++;
      log.warn("history_refresh_failed", { coin: id, kind: err instanceof MarketDataError ? err.kind : "error" });
      if (err instanceof MarketDataError && err.kind === "rate_limited") break; // stop early; next run continues
    }
    await sleep(delay);
  }
  return { updated, failed };
}

/** refresh-fundamentals (daily, rotating): developer activity and genesis date per coin. */
export async function refreshFundamentals({ db, log }: JobContext, batch = 20) {
  const svc = getMarketService();
  const delay = getEnv().HISTORY_REFRESH_DELAY_MS;
  const { data: coins, error } = await db
    .from("cryptocurrencies")
    .select("id")
    .eq("is_active", true)
    .order("fundamentals_updated_at", { ascending: true, nullsFirst: true })
    .limit(batch);
  if (error) throw new Error(error.message);
  let updated = 0;
  for (const { id } of coins ?? []) {
    if (!svc.isSupported(id)) continue;
    try {
      const d = await svc.provider.getCoinDetails(id);
      if (d) {
        const genesis = d.genesisDate && /^\d{4}-\d{2}-\d{2}/.test(d.genesisDate) ? d.genesisDate.slice(0, 10) : null;
        await db
          .from("cryptocurrencies")
          .update({
            dev_commits_4w: d.developer?.commits4Weeks ?? null,
            dev_stars: d.developer?.stars ?? null,
            dev_contributors: d.developer?.contributors ?? null,
            genesis_date: genesis,
            homepage_url: d.homepageUrl,
            fundamentals_updated_at: new Date().toISOString(),
          })
          .eq("id", id);
        updated++;
      }
    } catch (err) {
      log.warn("fundamentals_refresh_failed", { coin: id, kind: err instanceof MarketDataError ? err.kind : "error" });
      if (err instanceof MarketDataError && err.kind === "rate_limited") break;
    }
    await sleep(delay);
  }
  return { updated };
}
