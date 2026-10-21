"use client";

import Link from "next/link";
import { useActionState } from "react";
import { Button, Field, inputClass, Notice } from "@/components/ui";
import type { FormState } from "@/lib/auth/validation";
import { forgotPasswordAction, loginAction, registerAction, resetPasswordAction } from "./actions";

function Message({ state }: { state: FormState }) {
  if (!state.message) return null;
  return <Notice tone={state.ok ? "positive" : "negative"}>{state.message}</Notice>;
}

const err = (s: FormState, k: string) => s.fieldErrors?.[k];
const aria = (s: FormState, k: string) => ({ "aria-invalid": Boolean(err(s, k)) || undefined, "aria-describedby": err(s, k) ? `${k}-error` : undefined });

export function LoginForm({ next }: { next: string }) {
  const [state, action, pending] = useActionState(loginAction, {});
  return (
    <form action={action} className="space-y-4" noValidate>
      <Message state={state} />
      <input type="hidden" name="next" value={next} />
      <Field id="email" label="Email" error={err(state, "email")}>
        <input id="email" name="email" type="email" autoComplete="email" required defaultValue={state.values?.email} className={inputClass} {...aria(state, "email")} />
      </Field>
      <Field id="password" label="Password" error={err(state, "password")}>
        <input id="password" name="password" type="password" autoComplete="current-password" required className={inputClass} {...aria(state, "password")} />
      </Field>
      <Button type="submit" className="w-full" disabled={pending}>
        {pending ? "Signing in…" : "Sign in"}
      </Button>
      <div className="flex justify-between text-sm">
        <Link href="/forgot-password" className="text-accent hover:underline">
          Forgot password?
        </Link>
        <Link href={`/register?next=${encodeURIComponent(next)}`} className="text-muted hover:text-fg">
          Create account
        </Link>
      </div>
    </form>
  );
}

export function RegisterForm({ next }: { next: string }) {
  const [state, action, pending] = useActionState(registerAction, {});
  if (state.ok) return <Message state={state} />;
  return (
    <form action={action} className="space-y-4" noValidate>
      <Message state={state} />
      <input type="hidden" name="next" value={next} />
      <Field id="displayName" label="Display name (optional)" error={err(state, "displayName")}>
        <input id="displayName" name="displayName" maxLength={80} autoComplete="nickname" defaultValue={state.values?.displayName} className={inputClass} />
      </Field>
      <Field id="email" label="Email" error={err(state, "email")}>
        <input id="email" name="email" type="email" autoComplete="email" required defaultValue={state.values?.email} className={inputClass} {...aria(state, "email")} />
      </Field>
      <Field id="password" label="Password" error={err(state, "password")} hint="At least 10 characters, including a letter and a number.">
        <input id="password" name="password" type="password" autoComplete="new-password" required minLength={10} className={inputClass} {...aria(state, "password")} />
      </Field>
      <Field id="confirmPassword" label="Confirm password" error={err(state, "confirmPassword")}>
        <input id="confirmPassword" name="confirmPassword" type="password" autoComplete="new-password" required className={inputClass} {...aria(state, "confirmPassword")} />
      </Field>
      <div className="space-y-1">
        <label className="flex items-start gap-2 text-sm text-muted">
          <input type="checkbox" name="acceptTerms" className="mt-1" {...aria(state, "acceptTerms")} />
          <span>
            I understand that research on this site is not financial advice and that cryptocurrencies carry a high risk of loss. I accept the terms of
            service.
          </span>
        </label>
        {err(state, "acceptTerms") ? (
          <p id="acceptTerms-error" role="alert" className="text-xs text-negative">
            {err(state, "acceptTerms")![0]}
          </p>
        ) : null}
      </div>
      <Button type="submit" className="w-full" disabled={pending}>
        {pending ? "Creating account…" : "Create account"}
      </Button>
      <p className="text-center text-sm text-muted">
        Already have an account?{" "}
        <Link href={`/login?next=${encodeURIComponent(next)}`} className="text-accent hover:underline">
          Sign in
        </Link>
      </p>
    </form>
  );
}

export function ForgotForm() {
  const [state, action, pending] = useActionState(forgotPasswordAction, {});
  if (state.ok) return <Message state={state} />;
  return (
    <form action={action} className="space-y-4" noValidate>
      <Message state={state} />
      <Field id="email" label="Email" error={err(state, "email")}>
        <input id="email" name="email" type="email" autoComplete="email" required className={inputClass} {...aria(state, "email")} />
      </Field>
      <Button type="submit" className="w-full" disabled={pending}>
        {pending ? "Sending…" : "Send reset link"}
      </Button>
    </form>
  );
}

export function ResetForm() {
  const [state, action, pending] = useActionState(resetPasswordAction, {});
  return (
    <form action={action} className="space-y-4" noValidate>
      <Message state={state} />
      <Field id="password" label="New password" error={err(state, "password")} hint="At least 10 characters, including a letter and a number.">
        <input id="password" name="password" type="password" autoComplete="new-password" required className={inputClass} {...aria(state, "password")} />
      </Field>
      <Field id="confirmPassword" label="Confirm new password" error={err(state, "confirmPassword")}>
        <input id="confirmPassword" name="confirmPassword" type="password" autoComplete="new-password" required className={inputClass} {...aria(state, "confirmPassword")} />
      </Field>
      <Button type="submit" className="w-full" disabled={pending}>
        {pending ? "Saving…" : "Set new password"}
      </Button>
    </form>
  );
}
