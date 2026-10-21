import "server-only";
import type { SupabaseClient } from "@supabase/supabase-js";
import { redirect } from "next/navigation";
import { cache } from "react";
import { TIER_RANK, type MembershipTier } from "@/config/plans";
import { ApiError } from "@/lib/http/api";
import { createSupabaseServerClient } from "@/lib/supabase/server";

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
  isAdmin: boolean;
  isStaff: boolean;
  supabase: SupabaseClient | null;
}

export const getViewer = cache(async (): Promise<Viewer> => {
  const supabase = await createSupabaseServerClient();
  if (!supabase) return { user: null, tier: "free", isAdmin: false, isStaff: false, supabase: null };

  const { data, error } = await supabase.auth.getClaims();
  const claims = error ? null : data?.claims;
  if (!claims?.sub) return { user: null, tier: "free", isAdmin: false, isStaff: false, supabase };

  const [tierRes, adminRes, staffRes] = await Promise.all([
    supabase.rpc("current_membership_tier"),
    supabase.rpc("is_admin"),
    supabase.rpc("is_staff"),
  ]);
  const tier = (typeof tierRes.data === "string" && tierRes.data in TIER_RANK ? tierRes.data : "free") as MembershipTier;
  return {
    user: { id: claims.sub, email: typeof claims.email === "string" ? claims.email : null },
    tier,
    isAdmin: adminRes.data === true,
    isStaff: staffRes.data === true,
    supabase,
  };
});

export function hasTier(viewer: Pick<Viewer, "tier">, required: MembershipTier) {
  return TIER_RANK[viewer.tier] >= TIER_RANK[required];
}

// ---------- page guards (redirect)

export async function requireUserPage(next = "/dashboard") {
  const v = await getViewer();
  if (!v.user || !v.supabase) redirect(`/login?next=${encodeURIComponent(next)}`);
  return v as Viewer & { user: SessionUser; supabase: SupabaseClient };
}

export async function requireStaffPage() {
  const v = await requireUserPage("/admin");
  if (!v.isStaff) redirect("/dashboard?error=forbidden");
  return v;
}

export async function requireAdminPage() {
  const v = await requireUserPage("/admin");
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
  if (!hasTier(v, required)) throw new ApiError("payment_required", `This feature requires a ${required === "founding" ? "Founding" : "Premium"} membership.`);
  return v;
}

export async function requireAdminApi() {
  const v = await requireUserApi();
  if (!v.isAdmin) throw new ApiError("forbidden", "Administrator access required.");
  return v;
}

export async function requireStaffApi() {
  const v = await requireUserApi();
  if (!v.isStaff) throw new ApiError("forbidden", "Staff access required.");
  return v;
}
