import { mapDbError } from "@/lib/db/errors";
import { authed } from "@/lib/http/authed";
import { ok, parseJsonBody } from "@/lib/http/api";
import { PaperOrderSchema } from "@/lib/paper/schema";

export const dynamic = "force-dynamic";

/** Place a virtual order. Validation and rate limits are enforced again in SQL; the engine fills it. */
export const POST = authed("POST /api/paper/orders", { perMinute: 30 }, async ({ req, viewer }) => {
  const b = await parseJsonBody(req, PaperOrderSchema);
  const { data, error } = await viewer.supabase.rpc("place_paper_order", {
    p_pair_id: b.pairId, p_side: b.side, p_type: b.type, p_quantity: b.quantity,
    p_limit_price: b.limitPrice ?? null, p_stop_loss: b.stopLoss ?? null, p_take_profit: b.takeProfit ?? null, p_signal_id: b.signalId ?? null,
  });
  if (error) throw mapDbError(error, "place paper order");
  return ok({ orderId: data as string, status: "pending", virtual: true }, { status: 202 });
});
