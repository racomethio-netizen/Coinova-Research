import { z } from "zod";
import { getViewer } from "@/lib/auth/session";
import { ApiError, clientIp, handler, ok, parseQuery } from "@/lib/http/api";
import { enforceRateLimit } from "@/lib/http/rate-limit";
import { getCandles, TimeframeSchema } from "@/server/nexus";

export const dynamic = "force-dynamic";

const Query = z.object({
  pairId: z.coerce.number().int().positive(),
  tf: TimeframeSchema,
  limit: z.coerce.number().int().min(10).max(1000).default(300),
});

/** Public OHLCV candles (final only), with source attribution handled by the page. */
export const GET = handler("GET /api/candles", async (req: Request) => {
  enforceRateLimit(`candles:${clientIp(req)}`, 120, 60_000);
  const q = parseQuery(req.url, Query);
  const { supabase } = await getViewer();
  if (!supabase) throw new ApiError("not_configured", "Market data storage is not configured.");
  const candles = await getCandles(supabase, q.pairId, q.tf, q.limit);
  return ok({ candles }, { headers: { "Cache-Control": "public, max-age=30" } });
});
