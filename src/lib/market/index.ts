import "server-only";
import { getEnv } from "@/lib/env";
import { CoinGeckoProvider } from "./coingecko";
import { MockMarketDataProvider } from "./mock";
import { MarketDataService, universeFor } from "./service";
import type { MarketDataProvider } from "./types";

/**
 * Process-wide market-data service. The cache is shared by all requests in
 * this server process so page views never trigger per-coin provider calls.
 */
let service: MarketDataService | undefined;

export function createProvider(): MarketDataProvider {
  const env = getEnv();
  if (env.MARKET_DATA_PROVIDER === "coingecko") {
    return new CoinGeckoProvider({ plan: env.COINGECKO_API_PLAN, apiKey: env.COINGECKO_API_KEY, timeoutMs: env.MARKET_DATA_TIMEOUT_MS });
  }
  return new MockMarketDataProvider();
}

export function getMarketService(): MarketDataService {
  if (!service) {
    const provider = createProvider();
    service = new MarketDataService(provider, universeFor(provider));
  }
  return service;
}

export type { MarketDataService };
