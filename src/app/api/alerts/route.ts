import { AlertInputSchema } from "@/lib/alerts/evaluate";
import { authed } from "@/lib/http/authed";
import { ok, parseJsonBody } from "@/lib/http/api";
import { createAlert, getAlertHistory, listAlerts } from "@/server/alerts";

export const dynamic = "force-dynamic";

export const GET = authed("GET /api/alerts", {}, async ({ viewer }) => {
  const [alerts, history] = await Promise.all([listAlerts(viewer.supabase, viewer.user.id), getAlertHistory(viewer.supabase, viewer.user.id)]);
  return ok({ alerts, history });
});

/** Alerts require Premium (also enforced by a database trigger via tier limits). */
export const POST = authed("POST /api/alerts", { tier: "premium", perMinute: 30 }, async ({ req, viewer }) => {
  const input = await parseJsonBody(req, AlertInputSchema);
  return ok(await createAlert(viewer.supabase, viewer.user.id, input), { status: 201 });
});
