"use client";

import { useRouter } from "next/navigation";
import { useId, useState } from "react";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/card";
import { Select } from "@/components/ui/input";
import { FieldError, Label } from "@/components/ui/label";
import type { Dictionary, Locale } from "@/i18n/dictionaries";
import { ApiRequestError, apiSend, apiUpload } from "@/lib/client-api";

type T = Dictionary["identity"];
const msg = (t: T, e: unknown) => (e instanceof ApiRequestError ? (t.errors[e.code as keyof T["errors"]] ?? t.errors.generic) : t.errors.generic);

/** Consent checkbox + redirect to the provider's sign-in page. */
export function FaydaStartForm({ t, lang }: { t: T; lang: Locale }) {
  const id = useId();
  const [agree, setAgree] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <form
      className="space-y-4"
      onSubmit={async (e) => {
        e.preventDefault();
        setError(null);
        setBusy(true);
        try {
          const { authorizationUrl } = await apiSend<{ authorizationUrl: string }>("POST", "/fayda/start", { acceptConsent: agree, locale: lang });
          window.location.assign(authorizationUrl);
        } catch (err) {
          setError(msg(t, err));
          setBusy(false);
        }
      }}
    >
      <div className="flex items-start gap-3">
        <input id={id} type="checkbox" checked={agree} onChange={(e) => setAgree(e.target.checked)} className="mt-1 size-5 shrink-0 accent-emerald-700" />
        <label htmlFor={id} className="text-sm">
          {t.faydaConsent}
        </label>
      </div>
      {error ? <Alert tone="error">{error}</Alert> : null}
      <Button type="submit" variant="navy" disabled={busy || !agree}>
        {t.faydaStart}
      </Button>
    </form>
  );
}

export function ManualConsentButton({ t }: { t: T }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  return (
    <Button
      type="button"
      variant="outline"
      disabled={busy}
      onClick={async () => {
        setBusy(true);
        try {
          await apiSend("POST", "/consents", { purpose: "IDENTITY_DOCUMENT_REVIEW" });
          router.refresh();
        } finally {
          setBusy(false);
        }
      }}
    >
      {t.manualConsentGive}
    </Button>
  );
}

export function ManualReviewForm({ t }: { t: T }) {
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
        const form = new FormData(el);
        const file = form.get("file");
        setError(null);
        if (!(file instanceof File) || file.size === 0) {
          setError(t.errors.file_required);
          return;
        }
        setBusy(true);
        try {
          await apiUpload("/identity/manual", form);
          el.reset();
          router.refresh();
        } catch (err) {
          setError(msg(t, err));
        } finally {
          setBusy(false);
        }
      }}
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <Label htmlFor={`${base}-type`}>{t.docType}</Label>
          <Select id={`${base}-type`} name="documentType" defaultValue="FAYDA_ID_CARD">
            {Object.entries(t.docTypes).map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <Label htmlFor={`${base}-file`}>{t.file}</Label>
          <input
            id={`${base}-file`}
            name="file"
            type="file"
            accept="image/jpeg,image/png,application/pdf,.jpg,.jpeg,.png,.pdf"
            capture="environment"
            aria-invalid={!!error || undefined}
            aria-describedby={error ? `${base}-err` : undefined}
            className="block min-h-11 w-full text-sm file:me-3 file:min-h-11 file:rounded-md file:border file:border-line file:bg-white file:px-3 file:font-semibold"
          />
        </div>
      </div>
      <FieldError id={`${base}-err`}>{error}</FieldError>
      <Button type="submit" disabled={busy}>
        {t.submit}
      </Button>
    </form>
  );
}

export function WithdrawManualButton({ t }: { t: T }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      disabled={busy}
      onClick={async () => {
        setBusy(true);
        try {
          await apiSend("DELETE", "/identity/manual");
          router.refresh();
        } finally {
          setBusy(false);
        }
      }}
    >
      {t.withdraw}
    </Button>
  );
}
