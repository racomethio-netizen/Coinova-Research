import { z } from "zod";
import { authed } from "@/lib/http/authed";
import { ok, parseJsonBody } from "@/lib/http/api";
import { markNotificationsRead } from "@/server/notifications";

export const dynamic = "force-dynamic";

const Body = z.object({ ids: z.union([z.literal("all"), z.array(z.uuid()).min(1).max(200)]) });

export const POST = authed("POST /api/notifications/read", {}, async ({ req, viewer }) => {
  const { ids } = await parseJsonBody(req, Body);
  await markNotificationsRead(viewer.supabase, viewer.user.id, ids);
  return ok({ updated: true });
});
