import "server-only";
import type { SupabaseClient } from "@supabase/supabase-js";
import { ApiError } from "@/lib/http/api";
import { logger } from "@/lib/logger";
import { requireServiceClient } from "@/lib/supabase/server";

/**
 * Delete the caller's account (web settings and mobile app). Refuses while a live
 * subscription would renew, so the user is not charged again. Retention and the
 * deletion audit entry are handled in SQL (migration 0010).
 */
export async function deleteOwnAccount(sb: SupabaseClient, userId: string): Promise<void> {
  const { data: open } = await sb.from("subscriptions").select("id, livemode, cancel_at_period_end").eq("user_id", userId).in("status", ["active", "trialing", "past_due"]);
  if (open?.some((s) => s.livemode && !s.cancel_at_period_end)) {
    throw new ApiError("conflict", "Cancel your active subscription before deleting your account, so you are not charged again.");
  }
  const { error } = await requireServiceClient().auth.admin.deleteUser(userId);
  if (error) {
    logger.error("account_delete_failed", { code: error.code });
    throw new ApiError("internal_error", "Could not delete your account. Please contact support.");
  }
  logger.info("account_deleted");
}
