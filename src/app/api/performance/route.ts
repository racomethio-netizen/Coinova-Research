import { getViewer } from "@/lib/auth/session";
import { ApiError, clientIp, handler, ok } from "@/lib/http/api";
import { enforceRateLimit } from "@/lib/http/rate-limit";
import { getPerformance, PERFORMANCE_MIN_SAMPLE, toPerformanceRow } from "@/server/nexus";

export const dynamic = "force-dynamic";

/** Forward results over the signals the caller may see. No rates below the minimum sample. */
export const GET = handler("GET /api/performance", async (req: Request) => {
  enforceRateLimit(`performance:${clientIp(req)}`, 60, 60_000);
  const { supabase } = await getViewer();
  if (!supabase) throw new ApiError("not_configured", "Performance data is not available on this server.");
  return ok({ minSample: PERFORMANCE_MIN_SAMPLE, rows: (await getPerformance(supabase)).map((r) => toPerformanceRow(r as Record<string, unknown>)) });
});
