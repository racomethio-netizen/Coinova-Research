import "server-only";
import type { FeatureKey, MembershipTier } from "@/config/plans";
import { requireFeatureApi, requireTierApi, requireUserApi, type Viewer, type SessionUser } from "@/lib/auth/session";
import type { SupabaseClient } from "@supabase/supabase-js";
import { getEnv } from "@/lib/env";
import { parseBearer } from "@/lib/auth/bearer";
import { ApiError, assertSameOrigin, handler } from "./api";
import { enforceRateLimit } from "./rate-limit";

type AuthedViewer = Viewer & { user: SessionUser; supabase: SupabaseClient };

interface Options {
  /** Minimum tier; defaults to any signed-in user. */
  tier?: MembershipTier;
  /** Required feature entitlement (checked after `tier`). */
  feature?: FeatureKey;
  /** Requests per minute per user (default 60). */
  perMinute?: number;
}

/**
 * Route handler wrapper for authenticated endpoints:
 * CSRF origin check on cookie-authenticated mutations → verified session or
 * bearer token → tier/feature check → per-user rate limit.
 *
 * Bearer requests (mobile) skip the origin check: they are authenticated only
 * by the Authorization header, never by cookies (see src/lib/auth/bearer.ts).
 */
export function authed<P = Record<string, string>>(
  name: string,
  opts: Options,
  fn: (ctx: { req: Request; viewer: AuthedViewer; params: P }) => Promise<Response>,
) {
  return handler(name, async (req: Request, route?: { params: Promise<P> }) => {
    const bearer = parseBearer(req.headers.get("authorization"));
    if (bearer === "invalid") throw new ApiError("unauthorized", "Malformed Authorization header.");
    if (!bearer) assertSameOrigin(req, getEnv().NEXT_PUBLIC_SITE_URL);
    const viewer = opts.tier ? await requireTierApi(opts.tier) : await requireUserApi();
    if (opts.feature) await requireFeatureApi(opts.feature);
    enforceRateLimit(`${name}:${viewer.user.id}`, opts.perMinute ?? 60, 60_000);
    const params = route?.params ? await route.params : ({} as P);
    return fn({ req, viewer, params });
  });
}
