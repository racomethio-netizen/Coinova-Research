import { z } from "zod";

/**
 * Zod schemas for the CoinGecko v3 endpoints we use. Schemas are permissive
 * about extra fields and nullability (CoinGecko returns null for unknown
 * values) but strict about types: a string where a number is expected fails
 * validation rather than being coerced.
 */

const num = z.number().finite().nullable().optional();

export const CgMarketItem = z.object({
  id: z.string().min(1),
  symbol: z.string(),
  name: z.string(),
  image: z.string().nullable().optional(),
  current_price: num,
  market_cap: num,
  market_cap_rank: z.number().int().nullable().optional(),
  fully_diluted_valuation: num,
  total_volume: num,
  high_24h: num,
  low_24h: num,
  price_change_percentage_24h: num,
  price_change_percentage_1h_in_currency: num,
  price_change_percentage_24h_in_currency: num,
  price_change_percentage_7d_in_currency: num,
  price_change_percentage_30d_in_currency: num,
  circulating_supply: num,
  total_supply: num,
  max_supply: num,
  ath: num,
  ath_change_percentage: num,
  last_updated: z.string().nullable().optional(),
});
export type CgMarketItem = z.infer<typeof CgMarketItem>;

export const CgMarkets = z.array(CgMarketItem);

const usdMap = z.record(z.string(), z.number().finite().nullable()).nullable().optional();

export const CgCoinDetails = z.object({
  id: z.string().min(1),
  symbol: z.string(),
  name: z.string(),
  hashing_algorithm: z.string().nullable().optional(),
  categories: z.array(z.string().nullable()).nullable().optional(),
  description: z.object({ en: z.string().nullable().optional() }).partial().nullable().optional(),
  links: z
    .object({
      homepage: z.array(z.string()).nullable().optional(),
      whitepaper: z.string().nullable().optional(),
      repos_url: z.object({ github: z.array(z.string()).nullable().optional() }).partial().nullable().optional(),
    })
    .partial()
    .nullable()
    .optional(),
  image: z.object({ large: z.string().nullable().optional(), small: z.string().nullable().optional() }).partial().nullable().optional(),
  genesis_date: z.string().nullable().optional(),
  market_cap_rank: z.number().int().nullable().optional(),
  market_data: z
    .object({
      current_price: usdMap,
      market_cap: usdMap,
      fully_diluted_valuation: usdMap,
      total_volume: usdMap,
      high_24h: usdMap,
      low_24h: usdMap,
      ath: usdMap,
      ath_change_percentage: usdMap,
      price_change_percentage_1h_in_currency: usdMap,
      price_change_percentage_24h_in_currency: usdMap,
      price_change_percentage_7d_in_currency: usdMap,
      price_change_percentage_30d_in_currency: usdMap,
      circulating_supply: num,
      total_supply: num,
      max_supply: num,
      last_updated: z.string().nullable().optional(),
    })
    .partial()
    .nullable()
    .optional(),
  developer_data: z
    .object({
      forks: num,
      stars: num,
      pull_request_contributors: num,
      commit_count_4_weeks: num,
    })
    .partial()
    .nullable()
    .optional(),
  last_updated: z.string().nullable().optional(),
});
export type CgCoinDetails = z.infer<typeof CgCoinDetails>;

const tuple = z.tuple([z.number().finite(), z.number().finite().nullable()]);
export const CgMarketChart = z.object({
  prices: z.array(tuple),
  market_caps: z.array(tuple).optional(),
  total_volumes: z.array(tuple).optional(),
});

export const CgGlobal = z.object({
  data: z.object({
    active_cryptocurrencies: z.number().int().nullable().optional(),
    total_market_cap: usdMap,
    total_volume: usdMap,
    market_cap_percentage: usdMap,
    market_cap_change_percentage_24h_usd: num,
    updated_at: z.number().int().nullable().optional(),
  }),
});
