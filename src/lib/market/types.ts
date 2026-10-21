/**
 * Internal, provider-neutral market-data types.
 *
 * All numeric fields are nullable: a provider may not report a value, and a
 * missing value must stay missing (never coerced to 0).
 */

export type MarketDataSource = "coingecko" | "mock";

export interface CoinMarket {
  id: string;
  symbol: string;
  name: string;
  imageUrl: string | null;
  marketCapRank: number | null;
  priceUsd: number | null;
  marketCapUsd: number | null;
  fullyDilutedValuationUsd: number | null;
  volume24hUsd: number | null;
  high24hUsd: number | null;
  low24hUsd: number | null;
  change1hPct: number | null;
  change24hPct: number | null;
  change7dPct: number | null;
  change30dPct: number | null;
  circulatingSupply: number | null;
  totalSupply: number | null;
  maxSupply: number | null;
  athUsd: number | null;
  athChangePct: number | null;
  /** Provider-reported last update time for this asset (ISO 8601), if any. */
  lastUpdated: string | null;
}

export interface CoinDetails extends CoinMarket {
  description: string | null;
  categories: string[];
  homepageUrl: string | null;
  whitepaperUrl: string | null;
  githubRepos: string[];
  genesisDate: string | null;
  hashingAlgorithm: string | null;
  developer: {
    commits4Weeks: number | null;
    stars: number | null;
    forks: number | null;
    contributors: number | null;
  } | null;
}

export type HistoryPeriod = "1d" | "7d" | "30d" | "90d" | "365d";

export interface PricePoint {
  /** Unix milliseconds. */
  t: number;
  priceUsd: number;
  marketCapUsd: number | null;
  volumeUsd: number | null;
}

export interface GlobalMarketData {
  totalMarketCapUsd: number | null;
  totalVolume24hUsd: number | null;
  btcDominancePct: number | null;
  ethDominancePct: number | null;
  marketCapChange24hPct: number | null;
  activeCryptocurrencies: number | null;
  updatedAt: string | null;
}

export interface SupplyData {
  coinId: string;
  circulatingSupply: number | null;
  totalSupply: number | null;
  maxSupply: number | null;
  marketCapUsd: number | null;
  fullyDilutedValuationUsd: number | null;
  priceUsd: number | null;
  lastUpdated: string | null;
}

export interface ListCoinsParams {
  page: number;
  perPage: number;
  ids?: readonly string[];
}

export interface MarketDataProvider {
  readonly source: MarketDataSource;
  /** Markets for a page of the configured universe (bulk endpoint). */
  getCoins(params: ListCoinsParams): Promise<CoinMarket[]>;
  /** Markets for specific IDs, batched internally. */
  getMarketData(coinIds: readonly string[]): Promise<CoinMarket[]>;
  getCoinDetails(coinId: string): Promise<CoinDetails | null>;
  getHistoricalPrices(coinId: string, period: HistoryPeriod): Promise<PricePoint[]>;
  getGlobalMarketData(): Promise<GlobalMarketData>;
  getSupplyData(coinId: string): Promise<SupplyData | null>;
}

/** Envelope returned to callers: data plus provenance and freshness. */
export interface MarketResult<T> {
  data: T;
  source: MarketDataSource;
  /** When this data was retrieved from the provider (ISO 8601). */
  fetchedAt: string;
  /** True when served from cache after a failed refresh, or past its freshness window. */
  stale: boolean;
}

export class MarketDataError extends Error {
  constructor(
    public readonly kind: "rate_limited" | "unavailable" | "invalid_response" | "not_found" | "timeout",
    message: string,
  ) {
    super(message);
    this.name = "MarketDataError";
  }
}
