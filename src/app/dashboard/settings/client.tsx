"use client";

import { useActionState, useState } from "react";
import { Button, Field, inputClass, Notice } from "@/components/ui";
import { api } from "@/lib/client/api";
import { useMutation } from "@/lib/client/use-mutation";
import { deleteAccountAction, updateProfileAction } from "./actions";

export function ProfileForm({ initial }: { initial: { displayName: string; timezone: string; emailNotifications: boolean; marketingEmails: boolean } }) {
  const [state, action, pending] = useActionState(updateProfileAction, {});
  return (
    <form action={action} className="grid max-w-xl gap-4">
      {state.message ? <Notice tone={state.ok ? "positive" : "negative"}>{state.message}</Notice> : null}
      <Field id="displayName" label="Display name" error={state.fieldErrors?.displayName}>
        <input id="displayName" name="displayName" defaultValue={initial.displayName} maxLength={80} className={inputClass} />
      </Field>
      <Field id="timezone" label="Time zone" error={state.fieldErrors?.timezone} hint="IANA name, e.g. Africa/Addis_Ababa or UTC.">
        <input id="timezone" name="timezone" defaultValue={initial.timezone} maxLength={64} className={inputClass} />
      </Field>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" name="emailNotifications" defaultChecked={initial.emailNotifications} /> Send alert notifications by email (for alerts that use email)
      </label>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" name="marketingEmails" defaultChecked={initial.marketingEmails} /> Product news and research digests
      </label>
      <div>
        <Button type="submit" disabled={pending}>
          {pending ? "Saving…" : "Save settings"}
        </Button>
      </div>
    </form>
  );
}

export function TelegramLink({ linked }: { linked: boolean }) {
  const [link, setLink] = useState<{ url: string | null; expiresAt: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const unlink = useMutation();
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          variant="secondary"
          onClick={async () => {
            setError(null);
            const res = await api<{ url: string | null; expiresAt: string }>("/api/telegram/link", { method: "POST", body: {} });
            if (res.ok) setLink(res.data);
            else setError(res.error.message);
          }}
        >
          {linked ? "Link a different chat" : "Generate Telegram link"}
        </Button>
        {linked ? (
          <Button size="sm" variant="ghost" disabled={unlink.pending} onClick={() => unlink.run("/api/telegram/link", "DELETE")}>
            Unlink
          </Button>
        ) : null}
      </div>
      {link?.url ? (
        <p>
          <a href={link.url} target="_blank" rel="noopener noreferrer" className="text-accent underline">
            Open Telegram to link
          </a>{" "}
          <span className="text-xs text-faint">(expires {new Date(link.expiresAt).toLocaleTimeString()}; single use)</span>
        </p>
      ) : null}
      {error || unlink.error ? (
        <p role="alert" className="text-xs text-negative">
          {error ?? unlink.error}
        </p>
      ) : null}
    </div>
  );
}

export function DeleteAccountForm() {
  const [state, action, pending] = useActionState(deleteAccountAction, {});
  return (
    <form action={action} className="grid max-w-md gap-3">
      {state.message ? <Notice tone="negative">{state.message}</Notice> : null}
      <Field id="confirm" label='Type "DELETE" to confirm' error={state.fieldErrors?.confirm}>
        <input id="confirm" name="confirm" autoComplete="off" className={inputClass} />
      </Field>
      <div>
        <Button type="submit" variant="danger" disabled={pending}>
          Permanently delete my account
        </Button>
      </div>
    </form>
  );
}
