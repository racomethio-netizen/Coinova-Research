"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { z } from "zod";
import { requireUserPage } from "@/lib/auth/session";
import { safeNextPath, type FormState } from "@/lib/auth/validation";
import { rateLimit } from "@/lib/http/rate-limit";
import { logger } from "@/lib/logger";

const PAGE = "/dashboard/settings/security";

export type EnrollResult = { ok: true; factorId: string; qrCode: string; secret: string } | { ok: false; message: string };

/**
 * Start TOTP enrollment. All MFA calls run server-side with the user's own
 * session (the browser never talks to Supabase directly; CSP connect-src 'self').
 */
export async function enrollTotpAction(): Promise<EnrollResult> {
  const viewer = await requireUserPage(PAGE);
  if (!rateLimit(`mfa-enroll:${viewer.user.id}`, 5, 600_000).allowed) return { ok: false, message: "Too many attempts. Try again later." };
  const mfa = viewer.supabase.auth.mfa;
  // Remove abandoned, never-verified factors so they don't accumulate.
  const { data: list } = await mfa.listFactors();
  for (const f of list?.all ?? []) {
    if (f.status === "unverified") await mfa.unenroll({ factorId: f.id }).catch(() => undefined);
  }
  const { data, error } = await mfa.enroll({ factorType: "totp", friendlyName: `Authenticator ${new Date().toISOString().slice(0, 16)}` });
  if (error || !data || data.type !== "totp") {
    logger.warn("mfa_enroll_failed", { code: error?.code });
    return { ok: false, message: "Could not start two-factor setup. Please try again." };
  }
  return { ok: true, factorId: data.id, qrCode: data.totp.qr_code, secret: data.totp.secret };
}

const VerifySchema = z.object({
  factorId: z.uuid(),
  code: z.string().trim().regex(/^\d{6}$/, "Enter the 6-digit code from your authenticator app."),
  next: z.string().optional(),
});

/** Verify a TOTP code: completes enrollment, or upgrades the session to aal2. */
export async function verifyTotpAction(_prev: FormState, form: FormData): Promise<FormState> {
  const viewer = await requireUserPage(PAGE);
  const parsed = VerifySchema.safeParse(Object.fromEntries(form));
  if (!parsed.success) return { ok: false, fieldErrors: z.flattenError(parsed.error).fieldErrors as FormState["fieldErrors"] };
  if (!rateLimit(`mfa-verify:${viewer.user.id}`, 10, 600_000).allowed) return { ok: false, message: "Too many attempts. Wait a few minutes and try again." };
  const { error } = await viewer.supabase.auth.mfa.challengeAndVerify({ factorId: parsed.data.factorId, code: parsed.data.code });
  if (error) {
    logger.info("mfa_verify_rejected", { code: error.code });
    return { ok: false, fieldErrors: { code: ["That code was not accepted. Check your device's clock and try again."] } };
  }
  logger.info("mfa_verified");
  revalidatePath("/", "layout");
  redirect(safeNextPath(parsed.data.next, PAGE));
}

/** Remove a factor. Supabase requires an aal2 session to remove a verified factor. */
export async function unenrollFactorAction(_prev: FormState, form: FormData): Promise<FormState> {
  const viewer = await requireUserPage(PAGE);
  const factorId = z.uuid().safeParse(form.get("factorId"));
  if (!factorId.success) return { ok: false, message: "Unknown factor." };
  if (!rateLimit(`mfa-unenroll:${viewer.user.id}`, 5, 600_000).allowed) return { ok: false, message: "Too many attempts." };
  const { error } = await viewer.supabase.auth.mfa.unenroll({ factorId: factorId.data });
  if (error) {
    logger.info("mfa_unenroll_rejected", { code: error.code });
    return { ok: false, message: "Could not remove this authenticator. Verify a code first, then try again." };
  }
  logger.info("mfa_unenrolled");
  revalidatePath(PAGE);
  return { ok: true, message: "Authenticator removed." };
}
