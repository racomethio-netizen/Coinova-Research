import type { z } from "zod";
import { fetchWithTimeout, HttpError, parseRetryAfter, TimeoutError, withRetry, type RetryOptions } from "@/lib/http/fetch";
import { logger } from "@/lib/logger";
import { CgCoinDetails, CgGlobal, CgMarketChart, CgMarkets, type CgMarketItem } from "./coingecko-schemas";
import {
  MarketDataError,
  type CoinDetails,
  type CoinMarket,
  type GlobalMarketData,
  type HistoryPeriod,
  type ListCoinsParams,
  type MarketDataProvider,
  type PricePoint,
  type SupplyData,
} from "./types";

/**
 * CoinGecko v3 adapter.
 *
 * - Demo plan: https://api.coingecko.com/api/v3 with header `x-cg-demo-api-key`.
 * - Pro plan:  https://pro-api.coingecko.com/api/v3 with header `x-cg-pro-api-key`.
 * - Public (keyless): https://api.coingecko.com/api/v3, heavily rate limited.
 *
 * Keys are sent as headers (never query strings) and this module is only
 * used server-side.
 */

export interface CoinGeckoOptions {
  plan: "public" | "demo" | "pro";
  apiKey?: string;
  timeoutMs: number;
  fetchImpl?: typeof fetch;
  retry?: Partial<RetryOptions>;
}

/** CoinGecko /coins/markets accepts at most 250 per page. */
export const MAX_PER_PAGE = 250;
const PERIOD_DAYS: Record<HistoryPeriod, string> = { "1d": "1", "7d": "7", "30d": "30", "90d": "90", "365d": "365" };
const ID_PATTERN = /^[a-z0-9][a-z0-9-]{0,99}$/;

export function isValidCoinId(id: string): boolean {
  return ID_PATTERN.test(id);
}

export class CoinGeckoProvider implements MarketDataProvider {
  readonly source = "coingecko" as const;
  private readonly baseUrl: string;
  private readonly headers: Record<string, string>;
  private readonly retry: RetryOptions;

  constructor(private readonly opts: CoinGeckoOptions) {
    this.baseUrl = opts.plan === "pro" ? "https://pro-api.coingecko.com/api/v3" : "https://api.coingecko.com/api/v3";
    this.headers = { accept: "application/json" };
    if (opts.plan === "pro" && opts.apiKey) this.headers["x-cg-pro-api-key"] = opts.apiKey;
    if (opts.plan === "demo" && opts.apiKey) this.headers["x-cg-demo-api-key"] = opts.apiKey;
    this.retry = { retries: 3, baseDelayMs: 500, maxDelayMs: 8_000, ...opts.retry };
  }

  private async request<S extends z.ZodType>(path: string, params: Record<string, string>, schema: S): Promise<z.infer<S>> {
    const url = new URL(this.baseUrl + path);
    for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v);

    const json = await withRetry(async () => {
      let res: Response;
      try {
        res = await fetchWithTimeout(url, { headers: this.headers, timeoutMs: this.opts.timeoutMs, fetchImpl: this.opts.fetchImpl, cache: "no-store" });
      } catch (err) {
        if (err instanceof TimeoutError) throw err;
        throw err instanceof Error ? err : new Error(String(err));
      }
      if (res.status === 404) throw new HttpError(404, "Not found");
      if (!res.ok) throw new HttpError(res.status, `CoinGecko responded ${res.status}`, parseRetryAfter(res.headers.get("retry-after")));
      try {
        return (await res.json()) as unknown;
      } catch {
        throw new MarketDataError("invalid_response", "CoinGecko returned malformed JSON.");
      }
    }, this.retry).catch((err: unknown) => {
      throw this.normalizeError(err, path);
    });

    const parsed = schema.safeParse(json);
    if (!parsed.success) {
      logger.warn("coingecko_invalid_response", { path, issues: parsed.error.issues.slice(0, 5).map((i) => `${i.path.join(".")}: ${i.message}`) });
      throw new MarketDataError("invalid_response", "CoinGecko returned data in an unexpected format.");
    }
    return parsed.data;
  }

  private normalizeError(err: unknown, path: string): MarketDataError {
    if (err instanceof MarketDataError) return err;
    if (err instanceof TimeoutError) return new MarketDataError("timeout", "Market data provider timed out.");
    if (err instanceof HttpError) {
      if (err.status === 404) return new MarketDataError("not_found", "Asset not found at provider.");
      if (err.status === 429) {
        logger.warn("coingecko_rate_limited", { path });
        return new MarketDataError("rate_limited", "Market data provider rate limit reached.");
      }
      return new MarketDataError("unavailable", `Market data provider error (${err.status}).`);
    }
    return new MarketDataError("unavailable", "Market data provider unreachable.");
  }

  async getCoins({ page, perPage, ids }: ListCoinsParams): Promise<CoinMarket[]> {
    const params: Record<string, string> = {
      vs_currency: "usd",
      order: "market_cap_desc",
      per_page: String(Math.min(MAX_PER_PAGE, Math.max(1, perPage))),
      page: String(Math.max(1, page)),
      sparkline: "false",
      price_change_percentage: "1h,24h,7d,30d",
    };
    if (ids?.length) params.ids = ids.filter(isValidCoinId).join(",");
    const items = await this.request("/coins/markets", params, CgMarkets);
    return items.map(mapMarketItem);
  }

  async getMarketData(coinIds: readonly string[]): Promise<CoinMarket[]> {
    const ids = [...new Set(coinIds.filter(isValidCoinId))];
    const out: CoinMarket[] = [];
    // Bulk endpoint, batched by the per-page maximum.
    for (let i = 0; i < ids.length; i += MAX_PER_PAGE) {
      const batch = ids.slice(i, i + MAX_PER_PAGE);
      out.push(...(await this.getCoins({ page: 1, perPage: batch.length, ids: batch })));
    }
    return out;
  }

  async getCoinDetails(coinId: string): Promise<CoinDetails | null> {
    if (!isValidCoinId(coinId)) return null;
    try {
      const d = await this.request(
        `/coins/${encodeURIComponent(coinId)}`,
        { localization: "false", tickers: "false", market_data: "true", community_data: "false", developer_data: "true", sparkline: "false" },
        CgCoinDetails,
      );
      return mapDetails(d);
    } catch (err) {
      if (err instanceof MarketDataError && err.kind === "not_found") return null;
      throw err;
    }
  }

  async getHistoricalPrices(coinId: string, period: HistoryPeriod): Promise<PricePoint[]> {
    if (!isValidCoinId(coinId)) return [];
    const params: Record<string, string> = { vs_currency: "usd", days: PERIOD_DAYS[period] };
    if (period === "365d" || period === "90d") params.interval = "daily";
    const chart = await this.request(`/coins/${encodeURIComponent(coinId)}/market_chart`, params, CgMarketChart);
    return mapChart(chart);
  }

  async getGlobalMarketData(): Promise<GlobalMarketData> {
    const g = await this.request("/global", {}, CgGlobal);
    const d = g.data;
    return {
      totalMarketCapUsd: d.total_market_cap?.usd ?? null,
      totalVolume24hUsd: d.total_volume?.usd ?? null,
      btcDominancePct: d.market_cap_percentage?.btc ?? null,
      ethDominancePct: d.market_cap_percentage?.eth ?? null,
      marketCapChange24hPct: d.market_cap_change_percentage_24h_usd ?? null,
      activeCryptocurrencies: d.active_cryptocurrencies ?? null,
      updatedAt: d.updated_at ? new Date(d.updated_at * 1000).toISOString() : null,
    };
  }

  async getSupplyData(coinId: string): Promise<SupplyData | null> {
    const [m] = await this.getMarketData([coinId]);
    return m ? supplyFromMarket(m) : null;
  }
}

const n = (v: number | null | undefined): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
/** Supplies and prices cannot be negative; treat negative values as missing. */
const nonNeg = (v: number | null | undefined): number | null => {
  const x = n(v);
  return x !== null && x >= 0 ? x : null;
};

export function mapMarketItem(i: CgMarketItem): CoinMarket {
  return {
    id: i.id,
    symbol: i.symbol.toUpperCase(),
    name: i.name,
    imageUrl: safeHttpsUrl(i.image),
    marketCapRank: i.market_cap_rank ?? null,
    priceUsd: nonNeg(i.current_price),
    marketCapUsd: nonNeg(i.market_cap),
    fullyDilutedValuationUsd: nonNeg(i.fully_diluted_valuation),
    volume24hUsd: nonNeg(i.total_volume),
    high24hUsd: nonNeg(i.high_24h),
    low24hUsd: nonNeg(i.low_24h),
    change1hPct: n(i.price_change_percentage_1h_in_currency),
    change24hPct: n(i.price_change_percentage_24h_in_currency ?? i.price_change_percentage_24h),
    change7dPct: n(i.price_change_percentage_7d_in_currency),
    change30dPct: n(i.price_change_percentage_30d_in_currency),
    circulatingSupply: nonNeg(i.circulating_supply),
    totalSupply: nonNeg(i.total_supply),
    maxSupply: nonNeg(i.max_supply),
    athUsd: nonNeg(i.ath),
    athChangePct: n(i.ath_change_percentage),
    lastUpdated: validIso(i.last_updated),
  };
}

function mapDetails(d: z.infer<typeof CgCoinDetails>): CoinDetails {
  const md = d.market_data ?? {};
  const usd = (m: Record<string, number | null> | null | undefined) => n(m?.usd);
  return {
    id: d.id,
    symbol: d.symbol.toUpperCase(),
    name: d.name,
    imageUrl: safeHttpsUrl(d.image?.large ?? d.image?.small),
    marketCapRank: d.market_cap_rank ?? null,
    priceUsd: nonNeg(usd(md.current_price)),
    marketCapUsd: nonNeg(usd(md.market_cap)),
    fullyDilutedValuationUsd: nonNeg(usd(md.fully_diluted_valuation)),
    volume24hUsd: nonNeg(usd(md.total_volume)),
    high24hUsd: nonNeg(usd(md.high_24h)),
    low24hUsd: nonNeg(usd(md.low_24h)),
    change1hPct: usd(md.price_change_percentage_1h_in_currency),
    change24hPct: usd(md.price_change_percentage_24h_in_currency),
    change7dPct: usd(md.price_change_percentage_7d_in_currency),
    change30dPct: usd(md.price_change_percentage_30d_in_currency),
    circulatingSupply: nonNeg(md.circulating_supply),
    totalSupply: nonNeg(md.total_supply),
    maxSupply: nonNeg(md.max_supply),
    athUsd: nonNeg(usd(md.ath)),
    athChangePct: usd(md.ath_change_percentage),
    lastUpdated: validIso(md.last_updated ?? d.last_updated),
    description: d.description?.en ? stripHtml(d.description.en).slice(0, 4000) || null : null,
    categories: (d.categories ?? []).filter((c): c is string => typeof c === "string" && c.length > 0),
    homepageUrl: safeHttpsUrl(d.links?.homepage?.find((h) => h && h.trim())),
    whitepaperUrl: safeHttpsUrl(d.links?.whitepaper),
    githubRepos: (d.links?.repos_url?.github ?? []).map((u) => safeHttpsUrl(u)).filter((u): u is string => !!u).slice(0, 5),
    genesisDate: d.genesis_date ?? null,
    hashingAlgorithm: d.hashing_algorithm ?? null,
    developer: d.developer_data
      ? {
          commits4Weeks: nonNeg(d.developer_data.commit_count_4_weeks),
          stars: nonNeg(d.developer_data.stars),
          forks: nonNeg(d.developer_data.forks),
          contributors: nonNeg(d.developer_data.pull_request_contributors),
        }
      : null,
  };
}

function mapChart(c: z.infer<typeof CgMarketChart>): PricePoint[] {
  const caps = new Map((c.market_caps ?? []).map(([t, v]) => [t, v]));
  const vols = new Map((c.total_volumes ?? []).map(([t, v]) => [t, v]));
  const points: PricePoint[] = [];
  for (const [t, price] of c.prices) {
    if (price === null || price < 0) continue;
    points.push({ t, priceUsd: price, marketCapUsd: caps.get(t) ?? null, volumeUsd: vols.get(t) ?? null });
  }
  points.sort((a, b) => a.t - b.t);
  // Drop duplicate timestamps (CoinGecko sometimes repeats the latest point).
  return points.filter((p, i) => i === 0 || p.t !== points[i - 1]!.t);
}

export function supplyFromMarket(m: CoinMarket): SupplyData {
  return {
    coinId: m.id,
    circulatingSupply: m.circulatingSupply,
    totalSupply: m.totalSupply,
    maxSupply: m.maxSupply,
    marketCapUsd: m.marketCapUsd,
    fullyDilutedValuationUsd: m.fullyDilutedValuationUsd,
    priceUsd: m.priceUsd,
    lastUpdated: m.lastUpdated,
  };
}

export function stripHtml(s: string): string {
  return s
    .replace(/<[^>]*>/g, "")
    .replace(/&amp;/g, "&")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/\r\n/g, "\n")
    .trim();
}

export function safeHttpsUrl(u: string | null | undefined): string | null {
  if (!u) return null;
  try {
    const url = new URL(u.trim());
    return url.protocol === "https:" ? url.toString() : null;
  } catch {
    return null;
  }
}

function validIso(s: string | null | undefined): string | null {
  if (!s) return null;
  const t = Date.parse(s);
  return Number.isFinite(t) ? new Date(t).toISOString() : null;
}
