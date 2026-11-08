"use client";

import { useId, useState } from "react";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/input";
import { FieldError, Label } from "@/components/ui/label";
import type { Dictionary } from "@/i18n/dictionaries";
import { ApiRequestError, apiSend } from "@/lib/client-api";

export function ReportForm({ t, seller }: { t: Dictionary["report"]; seller: string }) {
  const base = useId();
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (sent) return <Alert tone="success" data-testid="report-thanks">{t.thanks}</Alert>;
  return (
    <form
      className="space-y-4"
      noValidate
      onSubmit={async (e) => {
        e.preventDefault();
        const f = new FormData(e.currentTarget);
        setBusy(true);
        setError(null);
        try {
          await apiSend("POST", "/reports", { seller: f.get("seller"), category: f.get("category"), description: f.get("description"), contact: f.get("contact") });
          setSent(true);
        } catch (err) {
          setError(err instanceof ApiRequestError ? (t.errors[err.code as keyof typeof t.errors] ?? t.errors.generic) : t.errors.generic);
        } finally {
          setBusy(false);
        }
      }}
    >
      <div>
        <Label htmlFor={`${base}-s`}>{t.sellerLabel}</Label>
        <Input id={`${base}-s`} name="seller" defaultValue={seller} required className="font-mono" autoCapitalize="characters" spellCheck={false} />
      </div>
      <div>
        <Label htmlFor={`${base}-c`}>{t.category}</Label>
        <Select id={`${base}-c`} name="category" defaultValue="NON_DELIVERY">
          {Object.entries(t.categories).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </Select>
      </div>
      <div>
        <Label htmlFor={`${base}-d`}>{t.description}</Label>
        <textarea id={`${base}-d`} name="description" required minLength={20} maxLength={5000} rows={5} aria-describedby={`${base}-dh ${error ? `${base}-e` : ""}`} aria-invalid={!!error || undefined} className="block w-full rounded-md border border-line px-3 py-2 text-base focus-visible:border-emerald-600 focus-visible:outline-2 focus-visible:outline-emerald-600" />
        <p id={`${base}-dh`} className="mt-1.5 text-sm text-muted">{t.descriptionHint}</p>
      </div>
      <div>
        <Label htmlFor={`${base}-ct`}>{t.contact}</Label>
        <Input id={`${base}-ct`} name="contact" maxLength={200} autoComplete="email" />
      </div>
      <FieldError id={`${base}-e`}>{error}</FieldError>
      <Button type="submit" disabled={busy}>{t.submit}</Button>
    </form>
  );
}
