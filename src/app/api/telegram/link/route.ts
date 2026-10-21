import { authed } from "@/lib/http/authed";
import { ok } from "@/lib/http/api";
import { createLinkToken, unlinkTelegram } from "@/server/telegram";

export const dynamic = "force-dynamic";

/** Issue a single-use, 15-minute Telegram link token (returned once as a t.me deep link). */
export const POST = authed("POST /api/telegram/link", { perMinute: 5 }, async ({ viewer }) => {
  return ok(await createLinkToken(viewer.user.id));
});

export const DELETE = authed("DELETE /api/telegram/link", { perMinute: 10 }, async ({ viewer }) => {
  await unlinkTelegram(viewer.supabase, viewer.user.id);
  return ok({ unlinked: true });
});
