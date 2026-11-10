"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { z } from "zod";
import { requireUserPage } from "@/lib/auth/session";
import type { FormState } from "@/lib/auth/validation";
import { rateLimit } from "@/lib/http/rate-limit";
import { ApiError } from "@/lib/http/api";
import { deleteOwnAccount } from "@/server/account";

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
  signalNotifications: z.literal("on").optional(),
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
      // Delivery also requires the signals.alerts entitlement (checked in SQL at publish time).
      signal_notifications: parsed.data.signalNotifications === "on",
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
  try {
    await deleteOwnAccount(viewer.supabase, viewer.user.id);
  } catch (e) {
    return { ok: false, message: e instanceof ApiError ? e.message : "Could not delete your account. Please contact support." };
  }
  await viewer.supabase.auth.signOut().catch(() => undefined);
  redirect("/?notice=account-deleted");
}
