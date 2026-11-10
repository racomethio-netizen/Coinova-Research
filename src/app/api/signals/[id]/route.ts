import { z } from "zod";
import { getViewer } from "@/lib/auth/session";
import { ApiError, clientIp, handler, ok } from "@/lib/http/api";
import { enforceRateLimit } from "@/lib/http/rate-limit";
import { getSignal } from "@/server/nexus";

export const dynamic = "force-dynamic";

export const GET = handler("GET /api/signals/[id]", async (req: Request, ctx: { params: Promise<{ id: string }> }) => {
  enforceRateLimit(`signal:${clientIp(req)}`, 120, 60_000);
  const id = z.uuid().safeParse((await ctx.params).id);
  if (!id.success) throw new ApiError("not_found", "Not found.");
  const { supabase } = await getViewer();
  if (!supabase) throw new ApiError("not_configured", "Signals are not available on this server.");
  const s = await getSignal(supabase, id.data);
  if (!s) throw new ApiError("not_found", "Not found."); // also when the gate or plan does not allow viewing it
  return ok({ signal: s }, { headers: { "Cache-Control": "private, no-store" } });
});
