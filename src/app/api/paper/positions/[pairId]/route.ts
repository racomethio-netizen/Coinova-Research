import { z } from "zod";
import { mapDbError } from "@/lib/db/errors";
import { authed } from "@/lib/http/authed";
import { ApiError, ok, parseJsonBody } from "@/lib/http/api";
import { PaperProtectionSchema } from "@/lib/paper/schema";

export const dynamic = "force-dynamic";

/** Set or clear stop loss / take profit on an open virtual position. */
export const PUT = authed<{ pairId: string }>("PUT /api/paper/positions/[pairId]", { perMinute: 30 }, async ({ req, viewer, params }) => {
  const pairId = z.coerce.number().int().positive().safeParse(params.pairId);
  if (!pairId.success) throw new ApiError("not_found", "Not found.");
  const b = await parseJsonBody(req, PaperProtectionSchema);
  const { data, error } = await viewer.supabase.rpc("set_paper_protection", { p_pair_id: pairId.data, p_stop_loss: b.stopLoss, p_take_profit: b.takeProfit });
  if (error) throw mapDbError(error, "set paper protection");
  if (!data) throw new ApiError("not_found", "No open position for this pair.");
  return ok({ updated: true });
});
