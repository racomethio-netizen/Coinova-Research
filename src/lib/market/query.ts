import { z } from "zod";
import type { CoinMarket } from "./types";

export const SORT_KEYS = ["rank", "name", "price", "change1h", "change24h", "change7d", "change30d", "marketCap", "volume"] as const;
export type SortKey = (typeof SORT_KEYS)[number];
export const CHANGE_PERIODS = ["1h", "24h", "7d", "30d"] as const;

export const MarketsQuerySchema = z.object({
  q: z.string().trim().max(50).optional().catch(undefined),
  sort: z.enum(SORT_KEYS).catch("rank").default("rank"),
  dir: z.enum(["asc", "desc"]).catch("asc").default("asc"),
  page: z.coerce.number().int().min(1).max(100).catch(1).default(1),
  perPage: z.coerce.number().int().min(10).max(100).catch(25).default(25),
  period: z.enum(CHANGE_PERIODS).catch("24h").default("24h"),
  filter: z.enum(["all", "gainers", "losers"]).catch("all").default("all"),
});
export type MarketsQuery = z.infer<typeof MarketsQuerySchema>;

const VALUE: Record<SortKey, (c: CoinMarket) => number | string | null> = {
  rank: (c) => c.marketCapRank,
  name: (c) => c.name.toLowerCase(),
  price: (c) => c.priceUsd,
  change1h: (c) => c.change1hPct,
  change24h: (c) => c.change24hPct,
  change7d: (c) => c.change7dPct,
  change30d: (c) => c.change30dPct,
  marketCap: (c) => c.marketCapUsd,
  volume: (c) => c.volume24hUsd,
};

export function changeFor(c: CoinMarket, period: MarketsQuery["period"]): number | null {
  return { "1h": c.change1hPct, "24h": c.change24hPct, "7d": c.change7dPct, "30d": c.change30dPct }[period];
}

export function queryMarkets(rows: readonly CoinMarket[], q: MarketsQuery) {
  let list = [...rows];
  if (q.q) {
    const term = q.q.toLowerCase();
    list = list.filter((c) => c.name.toLowerCase().includes(term) || c.symbol.toLowerCase().includes(term) || c.id.includes(term));
  }
  if (q.filter !== "all") {
    list = list.filter((c) => {
      const ch = changeFor(c, q.period);
      return ch !== null && (q.filter === "gainers" ? ch > 0 : ch < 0);
    });
  }
  const get = VALUE[q.sort];
  const mult = q.dir === "asc" ? 1 : -1;
  // Missing values always sort last regardless of direction.
  list.sort((a, b) => {
    const va = get(a);
    const vb = get(b);
    if (va === null && vb === null) return 0;
    if (va === null) return 1;
    if (vb === null) return -1;
    return (va < vb ? -1 : va > vb ? 1 : 0) * mult;
  });
  const total = list.length;
  const totalPages = Math.max(1, Math.ceil(total / q.perPage));
  const page = Math.min(q.page, totalPages);
  return { items: list.slice((page - 1) * q.perPage, page * q.perPage), total, page, totalPages };
}
