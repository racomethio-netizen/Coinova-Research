import { z } from "zod";
import { authed } from "@/lib/http/authed";
import { ApiError, ok, parseJsonBody } from "@/lib/http/api";
import { deleteWatchlist, renameWatchlist } from "@/server/watchlists";

export const dynamic = "force-dynamic";

const wid = (v: string) => {
  const r = z.uuid().safeParse(v);
  if (!r.success) throw new ApiError("not_found", "Watchlist not found.");
  return r.data;
};

export const PATCH = authed<{ id: string }>("PATCH /api/watchlists/[id]", { perMinute: 30 }, async ({ req, viewer, params }) => {
  const body = await parseJsonBody(req, z.object({ name: z.string().trim().min(1).max(60) }));
  await renameWatchlist(viewer.supabase, viewer.user.id, wid(params.id), body.name);
  return ok({ id: params.id, name: body.name });
});

export const DELETE = authed<{ id: string }>("DELETE /api/watchlists/[id]", { perMinute: 30 }, async ({ viewer, params }) => {
  await deleteWatchlist(viewer.supabase, viewer.user.id, wid(params.id));
  return ok({ deleted: true });
});
