"use client";

import { useActionState, useState } from "react";
import { Button, Field, inputClass, Notice } from "@/components/ui";
import { enrollTotpAction, unenrollFactorAction, verifyTotpAction, type EnrollResult } from "./actions";

export function VerifyCodeForm({ factorId, next, submitLabel = "Verify" }: { factorId: string; next?: string; submitLabel?: string }) {
  const [state, action, pending] = useActionState(verifyTotpAction, {});
  const err = state.fieldErrors?.code;
  return (
    <form action={action} className="grid max-w-xs gap-3">
      {state.message ? <Notice tone="negative">{state.message}</Notice> : null}
      <input type="hidden" name="factorId" value={factorId} />
      {next ? <input type="hidden" name="next" value={next} /> : null}
      <Field id="code" label="6-digit code" error={err}>
        <input
          id="code"
          name="code"
          inputMode="numeric"
          autoComplete="one-time-code"
          pattern="[0-9]{6}"
          maxLength={6}
          required
          aria-invalid={err ? true : undefined}
          aria-describedby={err ? "code-error" : undefined}
          className={inputClass}
        />
      </Field>
      <div>
        <Button type="submit" disabled={pending}>
          {pending ? "Verifying…" : submitLabel}
        </Button>
      </div>
    </form>
  );
}

export function EnrollTotp({ next }: { next?: string }) {
  const [enrollment, setEnrollment] = useState<EnrollResult | null>(null);
  const [pending, setPending] = useState(false);
  if (enrollment?.ok) {
    return (
      <div className="space-y-4">
        <ol className="list-decimal space-y-1 pl-5 text-sm text-muted">
          <li>Scan the QR code with an authenticator app (for example 1Password, Authy, or Google Authenticator).</li>
          <li>Or enter the setup key manually.</li>
          <li>Enter the 6-digit code the app shows.</li>
        </ol>
        {/* Supabase returns the QR code as an SVG data URI; next/image adds nothing here. */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={enrollment.qrCode} alt="QR code for your authenticator app" width={180} height={180} className="rounded-md bg-white p-2" />
        <p className="text-sm">
          Setup key: <code className="break-all rounded bg-elevated px-1.5 py-0.5 font-mono text-xs">{enrollment.secret}</code>
        </p>
        <VerifyCodeForm factorId={enrollment.factorId} next={next} submitLabel="Turn on two-factor" />
      </div>
    );
  }
  return (
    <div className="space-y-3">
      {enrollment && !enrollment.ok ? <Notice tone="negative">{enrollment.message}</Notice> : null}
      <Button
        disabled={pending}
        onClick={async () => {
          setPending(true);
          try {
            setEnrollment(await enrollTotpAction());
          } finally {
            setPending(false);
          }
        }}
      >
        {pending ? "Starting…" : "Set up authenticator app"}
      </Button>
    </div>
  );
}

export function RemoveFactor({ factorId, label }: { factorId: string; label: string }) {
  const [state, action, pending] = useActionState(unenrollFactorAction, {});
  return (
    <form action={action} className="flex flex-wrap items-center gap-2">
      <input type="hidden" name="factorId" value={factorId} />
      <Button type="submit" size="sm" variant="ghost" disabled={pending} aria-label={`Remove ${label}`}>
        {pending ? "Removing…" : "Remove"}
      </Button>
      {state.message ? <span className={state.ok ? "text-xs text-positive" : "text-xs text-negative"} role="status">{state.message}</span> : null}
    </form>
  );
}
