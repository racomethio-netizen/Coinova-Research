import { getViewer } from "@/lib/auth/session";
import { getNexusGates } from "@/lib/env";
import { ApiError, clientIp, handler, ok } from "@/lib/http/api";
import { enforceRateLimit } from "@/lib/http/rate-limit";
import { listSignals } from "@/server/nexus";

export const dynamic = "force-dynamic";

/** Signals visible to the caller (gate G-1 + tier, enforced by RLS). Cookie or bearer auth. */
export const GET = handler("GET /api/signals", async (req: Request) => {
  enforceRateLimit(`signals:${clientIp(req)}`, 60, 60_000);
  const { supabase } = await getViewer();
  if (!supabase) throw new ApiError("not_configured", "Signals are not available on this server.");
  return ok({ gate: getNexusGates().signalsVisibility, signals: await listSignals(supabase, 100) }, { headers: { "Cache-Control": "private, no-store" } });
});
