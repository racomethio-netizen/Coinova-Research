"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { z } from "zod";
import { requireUserPage } from "@/lib/auth/session";
import type { FormState } from "@/lib/auth/validation";
import { rateLimit } from "@/lib/http/rate-limit";
import { logger } from "@/lib/logger";
import { requireServiceClient } from "@/lib/supabase/server";

const ProfileSchema = z.object({
  displayName: z.string().trim().max(80).transform((v) => (v ? v : null)),
  timezone: z.string().trim().min(1).max(64).refine((tz) => {
    try {
      new Intl.DateTimeFormat("en-US", { timeZone: tz });
      return true;
    } catch {
      return false;
    }
  }, "Unknown time zone."),
  emailNotifications: z.literal("on").optional(),
  marketingEmails: z.literal("on").optional(),
});

export async function updateProfileAction(_prev: FormState, form: FormData): Promise<FormState> {
  const viewer = await requireUserPage("/dashboard/settings");
  const parsed = ProfileSchema.safeParse(Object.fromEntries(form));
  if (!parsed.success) return { ok: false, fieldErrors: z.flattenError(parsed.error).fieldErrors as FormState["fieldErrors"] };
  const { error } = await viewer.supabase
    .from("profiles")
    .update({
      display_name: parsed.data.displayName,
      timezone: parsed.data.timezone,
      email_notifications: parsed.data.emailNotifications === "on",
      marketing_emails: parsed.data.marketingEmails === "on",
    })
    .eq("id", viewer.user.id);
  if (error) return { ok: false, message: "Could not save your settings." };
  revalidatePath("/dashboard/settings");
  return { ok: true, message: "Settings saved." };
}

export async function deleteAccountAction(_prev: FormState, form: FormData): Promise<FormState> {
  const viewer = await requireUserPage("/dashboard/settings");
  if (form.get("confirm") !== "DELETE") return { ok: false, fieldErrors: { confirm: ['Type "DELETE" to confirm.'] } };
  if (!rateLimit(`delete-account:${viewer.user.id}`, 3, 3_600_000).allowed) return { ok: false, message: "Too many attempts." };
  const { data: open } = await viewer.supabase.from("subscriptions").select("id, livemode, cancel_at_period_end").eq("user_id", viewer.user.id).in("status", ["active", "trialing", "past_due"]);
  if (open?.some((s) => s.livemode && !s.cancel_at_period_end)) {
    return { ok: false, message: "Cancel your active subscription before deleting your account, so you are not charged again." };
  }
  const svc = requireServiceClient();
  const { error } = await svc.auth.admin.deleteUser(viewer.user.id);
  if (error) {
    logger.error("account_delete_failed", { code: error.code });
    return { ok: false, message: "Could not delete your account. Please contact support." };
  }
  logger.info("account_deleted");
  await viewer.supabase.auth.signOut().catch(() => undefined);
  redirect("/?notice=account-deleted");
}
