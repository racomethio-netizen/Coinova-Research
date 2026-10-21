import "server-only";
import type { MembershipTier } from "@/config/plans";
import { requireTierApi, requireUserApi, type Viewer, type SessionUser } from "@/lib/auth/session";
import type { SupabaseClient } from "@supabase/supabase-js";
import { getEnv } from "@/lib/env";
import { assertSameOrigin, handler } from "./api";
import { enforceRateLimit } from "./rate-limit";

type AuthedViewer = Viewer & { user: SessionUser; supabase: SupabaseClient };

interface Options {
  /** Minimum tier; defaults to any signed-in user. */
  tier?: MembershipTier;
  /** Requests per minute per user (default 60). */
  perMinute?: number;
}

/**
 * Route handler wrapper for cookie-authenticated endpoints:
 * CSRF origin check on mutations → verified session → tier check → per-user rate limit.
 */
export function authed<P = Record<string, string>>(
  name: string,
  opts: Options,
  fn: (ctx: { req: Request; viewer: AuthedViewer; params: P }) => Promise<Response>,
) {
  return handler(name, async (req: Request, route?: { params: Promise<P> }) => {
    assertSameOrigin(req, getEnv().NEXT_PUBLIC_SITE_URL);
    const viewer = opts.tier ? await requireTierApi(opts.tier) : await requireUserApi();
    enforceRateLimit(`${name}:${viewer.user.id}`, opts.perMinute ?? 60, 60_000);
    const params = route?.params ? await route.params : ({} as P);
    return fn({ req, viewer, params });
  });
}
