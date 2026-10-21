import { getViewer } from "@/lib/auth/session";
import { ApiError, clientIp, handler, ok } from "@/lib/http/api";
import { enforceRateLimit } from "@/lib/http/rate-limit";
import { getMarketService } from "@/lib/market";
import { isValidCoinId } from "@/lib/market/coingecko";
import { getScoreForCoin } from "@/lib/research/service";
import { computeSupplyMetrics } from "@/lib/research/tokenomics";

export const dynamic = "force-dynamic";

export const GET = handler("GET /api/coins/[id]", async (req: Request, ctx: { params: Promise<{ id: string }> }) => {
  enforceRateLimit(`coin:${clientIp(req)}`, 120, 60_000);
  const { id } = await ctx.params;
  const svc = getMarketService();
  if (!isValidCoinId(id) || !svc.isSupported(id)) throw new ApiError("not_found", "Unsupported or unknown coin.");
  const coin = await svc.getCoin(id).catch(() => {
    throw new ApiError("unavailable", "Market data provider is unavailable.");
  });
  if (!coin.data) throw new ApiError("not_found", "Coin not found at provider.");
  const viewer = await getViewer();
  const score = await getScoreForCoin(viewer, id).catch(() => ({ kind: "unavailable" as const }));
  return ok({
    coin: coin.data,
    supply: computeSupplyMetrics(coin.data),
    score,
    source: coin.source,
    fetchedAt: coin.fetchedAt,
    stale: coin.stale,
  });
});
