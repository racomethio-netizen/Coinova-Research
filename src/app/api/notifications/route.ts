import { authed } from "@/lib/http/authed";
import { ok } from "@/lib/http/api";
import { listNotifications } from "@/server/notifications";

export const dynamic = "force-dynamic";

export const GET = authed("GET /api/notifications", {}, async ({ viewer }) => {
  return ok({ notifications: await listNotifications(viewer.supabase, viewer.user.id) });
});
