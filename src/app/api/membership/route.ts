import { TIER_LIMITS } from "@/config/plans";
import { authed } from "@/lib/http/authed";
import { ok } from "@/lib/http/api";
import { getMembership } from "@/server/billing";

export const dynamic = "force-dynamic";

/** Current tier (from the database entitlement function), subscriptions, and payment history. */
export const GET = authed("GET /api/membership", {}, async ({ viewer }) => {
  const m = await getMembership(viewer.supabase, viewer.user.id);
  return ok({ tier: viewer.tier, limits: TIER_LIMITS[viewer.tier], ...m });
});
