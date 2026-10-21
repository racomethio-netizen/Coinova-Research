import { z } from "zod";
import { authed } from "@/lib/http/authed";
import { ApiError, ok, parseJsonBody } from "@/lib/http/api";
import { coinIdSchema } from "@/lib/portfolio/schemas";
import { addItem } from "@/server/watchlists";

export const dynamic = "force-dynamic";

export const POST = authed<{ id: string }>("POST /api/watchlists/[id]/items", { perMinute: 60 }, async ({ req, viewer, params }) => {
  const id = z.uuid().safeParse(params.id);
  if (!id.success) throw new ApiError("not_found", "Watchlist not found.");
  const body = await parseJsonBody(req, z.object({ coinId: coinIdSchema }));
  await addItem(viewer.supabase, viewer.user.id, id.data, body.coinId);
  return ok({ added: body.coinId }, { status: 201 });
});
