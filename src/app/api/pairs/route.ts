import { getViewer } from "@/lib/auth/session";
import { ApiError, clientIp, handler, ok } from "@/lib/http/api";
import { enforceRateLimit } from "@/lib/http/rate-limit";
import { listPairs } from "@/server/nexus";

export const dynamic = "force-dynamic";

export const GET = handler("GET /api/pairs", async (req: Request) => {
  enforceRateLimit(`pairs:${clientIp(req)}`, 60, 60_000);
  const { supabase } = await getViewer();
  if (!supabase) throw new ApiError("not_configured", "Market data storage is not configured.");
  return ok({ pairs: await listPairs(supabase) });
});
