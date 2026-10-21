import { z } from "zod";
import { ApiError, clientIp, handler, ok, parseQuery } from "@/lib/http/api";
import { enforceRateLimit } from "@/lib/http/rate-limit";
import { getMarketService } from "@/lib/market";
import { isValidCoinId } from "@/lib/market/coingecko";

export const dynamic = "force-dynamic";

const Query = z.object({ period: z.enum(["1d", "7d", "30d", "90d", "365d"]).default("30d") });

export const GET = handler("GET /api/coins/[id]/history", async (req: Request, ctx: { params: Promise<{ id: string }> }) => {
  enforceRateLimit(`history:${clientIp(req)}`, 60, 60_000);
  const { id } = await ctx.params;
  const { period } = parseQuery(req.url, Query);
  const svc = getMarketService();
  if (!isValidCoinId(id) || !svc.isSupported(id)) throw new ApiError("not_found", "Unsupported or unknown coin.");
  const history = await svc.getHistory(id, period).catch(() => {
    throw new ApiError("unavailable", "Historical data is temporarily unavailable.");
  });
  return ok(
    { points: history.data.map((p) => ({ t: p.t, priceUsd: p.priceUsd })), period, source: history.source, fetchedAt: history.fetchedAt, stale: history.stale },
    { headers: { "Cache-Control": "public, max-age=300" } },
  );
});
