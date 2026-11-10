import { getViewer } from "@/lib/auth/session";
import { ApiError, clientIp, handler, ok } from "@/lib/http/api";
import { enforceRateLimit } from "@/lib/http/rate-limit";
import { getScanner, toScannerRow } from "@/server/nexus";

export const dynamic = "force-dynamic";

/** Latest indicator states (no decisions). Public. */
export const GET = handler("GET /api/scanner", async (req: Request) => {
  enforceRateLimit(`scanner:${clientIp(req)}`, 60, 60_000);
  const { supabase } = await getViewer();
  if (!supabase) throw new ApiError("not_configured", "Market data storage is not configured.");
  return ok({ rows: (await getScanner(supabase)).map(toScannerRow) });
});
