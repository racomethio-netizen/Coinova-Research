"use client";

import { useRouter } from "next/navigation";
import { useId, useState } from "react";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/input";
import { FieldError, Label } from "@/components/ui/label";
import type { Dictionary } from "@/i18n/dictionaries";
import { ApiRequestError, apiSend } from "@/lib/client-api";

export function AppealForm({ t, suspended }: { t: Dictionary["appeals"]; suspended: boolean }) {
  const router = useRouter();
  const base = useId();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <form
      className="space-y-4"
      noValidate
      onSubmit={async (e) => {
        e.preventDefault();
        const el = e.currentTarget;
        const f = new FormData(el);
        setBusy(true);
        setError(null);
        try {
          await apiSend("POST", "/appeals", { subjectType: f.get("subjectType"), reason: f.get("reason") });
          el.reset();
          router.refresh();
        } catch (err) {
          setError(err instanceof ApiRequestError ? (t.errors[err.code as keyof typeof t.errors] ?? t.errors.generic) : t.errors.generic);
        } finally {
          setBusy(false);
        }
      }}
    >
      <div>
        <Label htmlFor={`${base}-s`}>{t.subject}</Label>
        <Select id={`${base}-s`} name="subjectType" defaultValue={suspended ? "ACCOUNT_SUSPENSION" : "DOCUMENT_REJECTION"}>
          {Object.entries(t.subjects)
            .filter(([v]) => suspended || v !== "ACCOUNT_SUSPENSION")
            .map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </Select>
      </div>
      <div>
        <Label htmlFor={`${base}-r`}>{t.reason}</Label>
        <textarea id={`${base}-r`} name="reason" required minLength={10} maxLength={5000} rows={4} aria-invalid={!!error || undefined} aria-describedby={error ? `${base}-e` : undefined} className="block w-full rounded-md border border-line px-3 py-2 text-base" />
      </div>
      <FieldError id={`${base}-e`}>{error}</FieldError>
      <Button type="submit" disabled={busy}>{t.submit}</Button>
    </form>
  );
}
