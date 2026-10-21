import { z } from "zod";
import { authed } from "@/lib/http/authed";
import { ok, parseJsonBody } from "@/lib/http/api";
import { coinIdSchema } from "@/lib/portfolio/schemas";
import { createWatchlist, listWatchlists } from "@/server/watchlists";

export const dynamic = "force-dynamic";

const CreateSchema = z.object({
  name: z.string().trim().min(1).max(60),
  coinIds: z.array(coinIdSchema).max(50).optional(),
});

export const GET = authed("GET /api/watchlists", {}, async ({ viewer }) => {
  return ok({ watchlists: await listWatchlists(viewer.supabase, viewer.user.id), tier: viewer.tier });
});

/** Tier limits (1 list for Free) are enforced by a database trigger. */
export const POST = authed("POST /api/watchlists", { perMinute: 20 }, async ({ req, viewer }) => {
  const body = await parseJsonBody(req, CreateSchema);
  return ok(await createWatchlist(viewer.supabase, viewer.user.id, body.name, body.coinIds ?? []), { status: 201 });
});
