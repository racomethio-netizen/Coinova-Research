import { COIN_UNIVERSE } from "@/config/coins";
import { StaleWhileErrorCache } from "./cache";
import { MockMarketDataProvider } from "./mock";
import type { CoinDetails, CoinMarket, GlobalMarketData, HistoryPeriod, MarketDataProvider, MarketResult, PricePoint } from "./types";

/**
 * Market-data service: provider + cache, returning data with provenance.
 *
 * Cache lifetimes reflect how often the underlying data changes. CoinGecko
 * refreshes /coins/markets roughly every 30–60s; history changes slowly.
 */
export const CACHE_POLICY = {
  markets: { ttlMs: 60_000, maxStaleMs: 30 * 60_000 },
  global: { ttlMs: 120_000, maxStaleMs: 60 * 60_000 },
  details: { ttlMs: 5 * 60_000, maxStaleMs: 6 * 3_600_000 },
  history: { ttlMs: 30 * 60_000, maxStaleMs: 24 * 3_600_000 },
} as const;

export class MarketDataService {
  private readonly cache: StaleWhileErrorCache;

  constructor(
    readonly provider: MarketDataProvider,
    private readonly universe: readonly string[],
    cache?: StaleWhileErrorCache,
  ) {
    this.cache = cache ?? new StaleWhileErrorCache();
  }

  get source() {
    return this.provider.source;
  }

  get universeIds(): readonly string[] {
    return this.universe;
  }

  private wrap<T>(v: { value: T; fetchedAt: number; stale: boolean }): MarketResult<T> {
    return { data: v.value, source: this.provider.source, fetchedAt: new Date(v.fetchedAt).toISOString(), stale: v.stale };
  }

  /** All markets in the supported universe (one or a few bulk calls, cached). */
  async getUniverseMarkets(): Promise<MarketResult<CoinMarket[]>> {
    const { ttlMs, maxStaleMs } = CACHE_POLICY.markets;
    const v = await this.cache.get("markets:universe", ttlMs, maxStaleMs, async () => {
      const rows = await this.provider.getMarketData(this.universe);
      return rows.sort((a, b) => (a.marketCapRank ?? Infinity) - (b.marketCapRank ?? Infinity));
    });
    return this.wrap(v);
  }

  async getGlobal(): Promise<MarketResult<GlobalMarketData>> {
    const { ttlMs, maxStaleMs } = CACHE_POLICY.global;
    return this.wrap(await this.cache.get("global", ttlMs, maxStaleMs, () => this.provider.getGlobalMarketData()));
  }

  async getCoin(coinId: string): Promise<MarketResult<CoinDetails | null>> {
    if (!this.universe.includes(coinId)) {
      return { data: null, source: this.provider.source, fetchedAt: new Date().toISOString(), stale: false };
    }
    const { ttlMs, maxStaleMs } = CACHE_POLICY.details;
    return this.wrap(await this.cache.get(`coin:${coinId}`, ttlMs, maxStaleMs, () => this.provider.getCoinDetails(coinId)));
  }

  async getHistory(coinId: string, period: HistoryPeriod): Promise<MarketResult<PricePoint[]>> {
    if (!this.universe.includes(coinId)) {
      return { data: [], source: this.provider.source, fetchedAt: new Date().toISOString(), stale: false };
    }
    const { ttlMs, maxStaleMs } = CACHE_POLICY.history;
    return this.wrap(await this.cache.get(`history:${coinId}:${period}`, ttlMs, maxStaleMs, () => this.provider.getHistoricalPrices(coinId, period)));
  }

  isSupported(coinId: string) {
    return this.universe.includes(coinId);
  }
}

export function universeFor(provider: MarketDataProvider): readonly string[] {
  return provider instanceof MockMarketDataProvider ? provider.universe() : COIN_UNIVERSE;
}
