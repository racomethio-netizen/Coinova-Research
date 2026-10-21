"use server";

import { headers } from "next/headers";
import { redirect } from "next/navigation";
import { z } from "zod";
import { ForgotSchema, LoginSchema, RegisterSchema, ResetSchema, safeNextPath, type FormState } from "@/lib/auth/validation";
import { getEnv } from "@/lib/env";
import { rateLimit } from "@/lib/http/rate-limit";
import { logger } from "@/lib/logger";
import { createSupabaseServerClient } from "@/lib/supabase/server";

async function ip() {
  const h = await headers();
  return h.get("x-forwarded-for")?.split(",")[0]?.trim() ?? h.get("x-real-ip") ?? "unknown";
}

function fieldErrors(err: z.ZodError) {
  return z.flattenError(err).fieldErrors as Record<string, string[] | undefined>;
}

const NOT_CONFIGURED: FormState = { ok: false, message: "Accounts are unavailable: authentication (Supabase) is not configured on this server." };
const TOO_MANY: FormState = { ok: false, message: "Too many attempts. Please wait a few minutes and try again." };

export async function registerAction(_prev: FormState, form: FormData): Promise<FormState> {
  const values = { email: String(form.get("email") ?? ""), displayName: String(form.get("displayName") ?? "") };
  const parsed = RegisterSchema.safeParse(Object.fromEntries(form));
  if (!parsed.success) return { ok: false, fieldErrors: fieldErrors(parsed.error), values };
  if (!rateLimit(`register:${await ip()}`, 5, 15 * 60_000).allowed) return TOO_MANY;

  const supabase = await createSupabaseServerClient();
  if (!supabase) return NOT_CONFIGURED;

  const flag = await supabase.from("feature_flags").select("enabled").eq("key", "public_signups").maybeSingle();
  if (flag.data && flag.data.enabled === false) return { ok: false, message: "New registrations are temporarily closed.", values };

  const next = safeNextPath(form.get("next"));
  const { data, error } = await supabase.auth.signUp({
    email: parsed.data.email,
    password: parsed.data.password,
    options: {
      emailRedirectTo: new URL(`/auth/callback?next=${encodeURIComponent(next)}`, getEnv().NEXT_PUBLIC_SITE_URL).toString(),
      data: parsed.data.displayName ? { display_name: parsed.data.displayName } : {},
    },
  });
  if (error) {
    logger.warn("register_failed", { code: error.code, status: error.status });
    // Weak password / invalid email are safe to surface; anything else is generic.
    if (error.code === "weak_password") return { ok: false, fieldErrors: { password: [error.message] }, values };
    return { ok: false, message: "We could not create your account. Please check your details and try again.", values };
  }
  // When email confirmation is enabled no session is returned. Supabase also
  // returns an obfuscated user for already-registered emails; the message is
  // identical either way to avoid account enumeration.
  if (!data.session) {
    return { ok: true, message: "Check your email to confirm your account, then sign in." };
  }
  redirect(next);
}

export async function loginAction(_prev: FormState, form: FormData): Promise<FormState> {
  const values = { email: String(form.get("email") ?? "") };
  const parsed = LoginSchema.safeParse(Object.fromEntries(form));
  if (!parsed.success) return { ok: false, fieldErrors: fieldErrors(parsed.error), values };
  const clientIp = await ip();
  if (!rateLimit(`login:${clientIp}`, 10, 15 * 60_000).allowed || !rateLimit(`login-email:${parsed.data.email}`, 10, 15 * 60_000).allowed) return TOO_MANY;

  const supabase = await createSupabaseServerClient();
  if (!supabase) return NOT_CONFIGURED;
  const { error } = await supabase.auth.signInWithPassword(parsed.data);
  if (error) {
    if (error.code === "email_not_confirmed") return { ok: false, message: "Please confirm your email address first. Check your inbox for the confirmation link.", values };
    return { ok: false, message: "Incorrect email or password.", values };
  }
  redirect(safeNextPath(form.get("next")));
}

export async function logoutAction() {
  const supabase = await createSupabaseServerClient();
  if (supabase) await supabase.auth.signOut();
  redirect("/");
}

export async function forgotPasswordAction(_prev: FormState, form: FormData): Promise<FormState> {
  const parsed = ForgotSchema.safeParse(Object.fromEntries(form));
  if (!parsed.success) return { ok: false, fieldErrors: fieldErrors(parsed.error) };
  if (!rateLimit(`forgot:${await ip()}`, 5, 15 * 60_000).allowed) return TOO_MANY;
  const supabase = await createSupabaseServerClient();
  if (!supabase) return NOT_CONFIGURED;
  const { error } = await supabase.auth.resetPasswordForEmail(parsed.data.email, {
    redirectTo: new URL("/auth/callback?next=/reset-password", getEnv().NEXT_PUBLIC_SITE_URL).toString(),
  });
  if (error) logger.warn("password_reset_request_failed", { code: error.code });
  // Same response whether or not the account exists.
  return { ok: true, message: "If an account exists for that email, a password reset link has been sent." };
}

export async function resetPasswordAction(_prev: FormState, form: FormData): Promise<FormState> {
  const parsed = ResetSchema.safeParse(Object.fromEntries(form));
  if (!parsed.success) return { ok: false, fieldErrors: fieldErrors(parsed.error) };
  const supabase = await createSupabaseServerClient();
  if (!supabase) return NOT_CONFIGURED;
  const { data } = await supabase.auth.getClaims();
  if (!data?.claims?.sub) return { ok: false, message: "Your reset link has expired. Please request a new one." };
  const { error } = await supabase.auth.updateUser({ password: parsed.data.password });
  if (error) {
    if (error.code === "same_password") return { ok: false, fieldErrors: { password: ["Choose a password different from your current one."] } };
    return { ok: false, message: "Could not update your password. Please request a new reset link." };
  }
  redirect("/dashboard?notice=password-updated");
}
