"use client";

import { useRouter } from "next/navigation";
import { useId, useState } from "react";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { FieldError, Label } from "@/components/ui/label";
import type { Dictionary, Locale } from "@/i18n/dictionaries";
import { ApiRequestError, apiSend } from "@/lib/client-api";

type T = Dictionary["privacy"];
const msg = (t: T, e: unknown) => (e instanceof ApiRequestError ? (t.errors[e.code as keyof T["errors"]] ?? t.errors.generic) : t.errors.generic);

export function ConsentToggle({ purpose, granted, t }: { purpose: string; granted: boolean; t: T }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  return (
    <Button
      type="button"
      size="sm"
      variant={granted ? "outline" : "default"}
      disabled={busy}
      onClick={async () => {
        setBusy(true);
        try {
          if (granted) await apiSend("DELETE", `/consents/${purpose}`);
          else await apiSend("POST", "/consents", { purpose });
          router.refresh();
        } finally {
          setBusy(false);
        }
      }}
    >
      {granted ? t.withdraw : t.give}
    </Button>
  );
}

export function CorrectionForm({ t }: { t: T }) {
  const router = useRouter();
  const id = useId();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <form
      className="space-y-3"
      noValidate
      onSubmit={async (e) => {
        e.preventDefault();
        const el = e.currentTarget;
        setBusy(true);
        setError(null);
        try {
          await apiSend("POST", "/privacy/requests/correction", { details: new FormData(el).get("details") });
          el.reset();
          router.refresh();
        } catch (err) {
          setError(msg(t, err));
        } finally {
          setBusy(false);
        }
      }}
    >
      <Label htmlFor={id}>{t.correctionLabel}</Label>
      <textarea id={id} name="details" rows={3} minLength={10} maxLength={2000} aria-invalid={!!error || undefined} aria-describedby={error ? `${id}-e` : undefined} className="block w-full rounded-md border border-line px-3 py-2 text-base" />
      <FieldError id={`${id}-e`}>{error}</FieldError>
      <Button type="submit" variant="outline" disabled={busy}>{t.correctionSubmit}</Button>
    </form>
  );
}

export function DeleteAccountForm({ t, lang }: { t: T; lang: Locale }) {
  const router = useRouter();
  const id = useId();
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);
  if (done) return <Alert tone="info" data-testid="deletion-done">{t.deleted}</Alert>;
  return (
    <form
      className="space-y-3"
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        setError(null);
        try {
          await apiSend("POST", "/privacy/deletion", { confirm: value });
          setDone(true);
          setTimeout(() => {
            router.push(`/${lang}`);
            router.refresh();
          }, 3000);
        } catch (err) {
          setError(msg(t, err));
          setBusy(false);
        }
      }}
    >
      <Label htmlFor={id}>{t.deleteLabel}</Label>
      <Input id={id} value={value} onChange={(e) => setValue(e.target.value)} autoComplete="off" spellCheck={false} className="max-w-xs font-mono" aria-invalid={!!error || undefined} aria-describedby={error ? `${id}-e` : undefined} />
      <FieldError id={`${id}-e`}>{error}</FieldError>
      <Button type="submit" variant="outline" className="border-red-700 text-red-800 hover:bg-red-50" disabled={busy || value !== "DELETE"}>
        {t.deleteButton}
      </Button>
    </form>
  );
}
