import { z } from "zod";
import { authed } from "@/lib/http/authed";
import { ApiError, ok } from "@/lib/http/api";
import { coinIdSchema } from "@/lib/portfolio/schemas";
import { removeItem } from "@/server/watchlists";

export const dynamic = "force-dynamic";

export const DELETE = authed<{ id: string; coinId: string }>("DELETE /api/watchlists/[id]/items/[coinId]", { perMinute: 60 }, async ({ viewer, params }) => {
  const id = z.uuid().safeParse(params.id);
  const coin = coinIdSchema.safeParse(params.coinId);
  if (!id.success || !coin.success) throw new ApiError("not_found", "Not found.");
  await removeItem(viewer.supabase, viewer.user.id, id.data, coin.data);
  return ok({ removed: coin.data });
});
