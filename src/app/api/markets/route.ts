import { ApiError, clientIp, handler, ok, parseQuery } from "@/lib/http/api";
import { enforceRateLimit } from "@/lib/http/rate-limit";
import { getMarketService } from "@/lib/market";
import { MarketsQuerySchema, queryMarkets } from "@/lib/market/query";

export const dynamic = "force-dynamic";

export const GET = handler("GET /api/markets", async (req: Request) => {
  enforceRateLimit(`markets:${clientIp(req)}`, 120, 60_000);
  const query = parseQuery(req.url, MarketsQuerySchema);
  const svc = getMarketService();
  const markets = await svc.getUniverseMarkets().catch(() => {
    throw new ApiError("unavailable", "Market data provider is unavailable and no recent cached data exists.");
  });
  const result = queryMarkets(markets.data, query);
  return ok(
    { ...result, source: markets.source, fetchedAt: markets.fetchedAt, stale: markets.stale },
    { headers: { "Cache-Control": "public, max-age=30, stale-while-revalidate=30" } },
  );
});
