import { z } from "zod";
import { authed } from "@/lib/http/authed";
import { ok, parseJsonBody } from "@/lib/http/api";
import { startCheckout } from "@/server/billing";

export const dynamic = "force-dynamic";

/** Only a plan id is accepted from the client; prices come from server-side configuration. */
const Body = z.object({ planId: z.enum(["premium_monthly", "premium_yearly", "founding_yearly"]) });

export const POST = authed("POST /api/billing/checkout", { perMinute: 10 }, async ({ req, viewer }) => {
  const { planId } = await parseJsonBody(req, Body);
  return ok(await startCheckout(viewer.supabase, viewer.user, planId));
});
