import "server-only";
import type { SupabaseClient } from "@supabase/supabase-js";
import { redirect } from "next/navigation";
import { cache } from "react";
import { minimumTierFor, normalizeTier, TIER_LABEL, TIER_RANK, tierHasFeature, type FeatureKey, type MembershipTier } from "@/config/plans";
import { ApiError } from "@/lib/http/api";
import { mfaState } from "./mfa";
import { createSupabaseBearerClient, createSupabaseServerClient } from "@/lib/supabase/server";
import { headers } from "next/headers";
import { parseBearer } from "./bearer";

/**
 * Data Access Layer for identity and authorization.
 *
 * Identity always comes from a verified Supabase JWT (getClaims), never from
 * client-supplied ids. Tier and roles always come from the database
 * (current_membership_tier / is_admin), never from client state.
 */

export interface SessionUser {
  id: string;
  email: string | null;
}

export interface Viewer {
  user: SessionUser | null;
  tier: MembershipTier;
  /** True only in an MFA-verified (aal2) session; see migration 0009. */
  isAdmin: boolean;
  isStaff: boolean;
  /** The user holds an admin/editor role but this session is not MFA-verified. */
  mfaRequired: boolean;
  supabase: SupabaseClient | null;
  /** How the request authenticated: session cookie (web) or bearer token (mobile). */
  authMethod: "cookie" | "bearer";
}


export const getViewer = cache(async (): Promise<Viewer> => {
  const bearer = parseBearer((await headers()).get("authorization"));
  if (bearer === "invalid") throw new ApiError("unauthorized", "Malformed Authorization header.");
  const authMethod = bearer ? "bearer" : "cookie";
  const supabase = bearer ? createSupabaseBearerClient(bearer) : await createSupabaseServerClient();
  const anonymous = { user: null, tier: "free", isAdmin: false, isStaff: false, mfaRequired: false, authMethod } as const;
  if (!supabase) return { ...anonymous, supabase: null };

  const { data, error } = bearer ? await supabase.auth.getClaims(bearer) : await supabase.auth.getClaims();
  const claims = error ? null : data?.claims;
  // An explicit but unverifiable token is an error, not an anonymous request.
  if (bearer && !claims?.sub) throw new ApiError("unauthorized", "Invalid or expired access token.");
  if (!claims?.sub) return { ...anonymous, supabase };

  const [tierRes, adminRes, staffRes, rolesRes] = await Promise.all([
    supabase.rpc("current_membership_tier"),
    supabase.rpc("is_admin"),
    supabase.rpc("is_staff"),
    supabase.from("user_roles").select("role"),
  ]);
  const roles = (rolesRes.data ?? []).map((r: { role: string }) => r.role);
  const tier = normalizeTier(tierRes.data);
  return {
    user: { id: claims.sub, email: typeof claims.email === "string" ? claims.email : null },
    tier,
    isAdmin: adminRes.data === true,
    isStaff: staffRes.data === true,
    mfaRequired: mfaState(roles, claims.aal).mfaRequired,
    supabase,
    authMethod,
  };
});

export function hasTier(viewer: Pick<Viewer, "tier">, required: MembershipTier) {
  return TIER_RANK[viewer.tier] >= TIER_RANK[required];
}

/** Feature entitlement for the viewer's server-derived tier. Mirrors SQL `has_feature`. */
export function hasFeature(viewer: Pick<Viewer, "tier">, feature: FeatureKey) {
  return tierHasFeature(viewer.tier, feature);
}

// ---------- page guards (redirect)

export async function requireUserPage(next = "/dashboard") {
  const v = await getViewer();
  if (!v.user || !v.supabase) redirect(`/login?next=${encodeURIComponent(next)}`);
  return v as Viewer & { user: SessionUser; supabase: SupabaseClient };
}

export const MFA_PAGE = "/dashboard/settings/security";

export async function requireStaffPage() {
  const v = await requireUserPage("/admin");
  if (!v.isStaff && v.mfaRequired) redirect(`${MFA_PAGE}?next=/admin`);
  if (!v.isStaff) redirect("/dashboard?error=forbidden");
  return v;
}

export async function requireAdminPage() {
  const v = await requireUserPage("/admin");
  if (!v.isAdmin && v.mfaRequired) redirect(`${MFA_PAGE}?next=/admin`);
  if (!v.isAdmin) redirect("/dashboard?error=forbidden");
  return v;
}

// ---------- API guards (throw ApiError)

export async function requireUserApi() {
  const v = await getViewer();
  if (!v.supabase) throw new ApiError("not_configured", "Accounts are not available: Supabase is not configured.");
  if (!v.user) throw new ApiError("unauthorized", "Sign in required.");
  return v as Viewer & { user: SessionUser; supabase: SupabaseClient };
}

export async function requireTierApi(required: MembershipTier) {
  const v = await requireUserApi();
  if (!hasTier(v, required)) throw new ApiError("payment_required", `This feature requires a ${TIER_LABEL[required]} membership or higher.`);
  return v;
}

export async function requireFeatureApi(feature: FeatureKey) {
  const v = await requireUserApi();
  if (!hasFeature(v, feature)) throw new ApiError("payment_required", `This feature requires a ${TIER_LABEL[minimumTierFor(feature)]} membership or higher.`);
  return v;
}

export async function requireAdminApi() {
  const v = await requireUserApi();
  if (!v.isAdmin && v.mfaRequired) throw new ApiError("forbidden", "Two-factor verification is required for administrator access.");
  if (!v.isAdmin) throw new ApiError("forbidden", "Administrator access required.");
  return v;
}

export async function requireStaffApi() {
  const v = await requireUserApi();
  if (!v.isStaff && v.mfaRequired) throw new ApiError("forbidden", "Two-factor verification is required for staff access.");
  if (!v.isStaff) throw new ApiError("forbidden", "Staff access required.");
  return v;
}
