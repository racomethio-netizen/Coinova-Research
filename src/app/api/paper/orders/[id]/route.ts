import { z } from "zod";
import { mapDbError } from "@/lib/db/errors";
import { authed } from "@/lib/http/authed";
import { ApiError, ok } from "@/lib/http/api";

export const dynamic = "force-dynamic";

export const DELETE = authed<{ id: string }>("DELETE /api/paper/orders/[id]", { perMinute: 30 }, async ({ viewer, params }) => {
  const id = z.uuid().safeParse(params.id);
  if (!id.success) throw new ApiError("not_found", "Not found.");
  const { data, error } = await viewer.supabase.rpc("cancel_paper_order", { p_order_id: id.data });
  if (error) throw mapDbError(error, "cancel paper order");
  if (!data) throw new ApiError("not_found", "No pending order with this id.");
  return ok({ cancelled: true });
});
