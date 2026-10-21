import { authed } from "@/lib/http/authed";
import { ok } from "@/lib/http/api";
import { requestCancellation } from "@/server/billing";

export const dynamic = "force-dynamic";

export const POST = authed("POST /api/billing/cancel", { perMinute: 5 }, async ({ viewer }) => {
  return ok(await requestCancellation(viewer.supabase, viewer.user.id));
});
