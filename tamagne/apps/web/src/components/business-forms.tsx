"use client";

import { useRouter } from "next/navigation";
import { useId, useState } from "react";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/input";
import { FieldError, Label } from "@/components/ui/label";
import type { Dictionary } from "@/i18n/dictionaries";
import { ApiRequestError, apiSend, apiUpload } from "@/lib/client-api";
import type { MyBusiness } from "@/lib/server-api";

type T = Dictionary["business"];

function errorText(t: T, e: unknown): string {
  if (e instanceof ApiRequestError) return t.errors[e.code as keyof T["errors"]] ?? t.errors.generic;
  return t.errors.generic;
}

export function BusinessDetailsForm({ t, business }: { t: T; business: MyBusiness | null }) {
  const router = useRouter();
  const base = useId();
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    setSaved(false);
    setFieldErrors({});
    setFormError(null);
    const f = new FormData(e.currentTarget);
    const body = Object.fromEntries(["legalName", "tradeName", "legalForm", "registrationNumber", "tinNumber", "region", "city"].map((k) => [k, String(f.get(k) ?? "")]));
    try {
      await apiSend(business ? "PATCH" : "POST", business ? "/businesses/me" : "/businesses", body);
      setSaved(true);
      router.refresh();
    } catch (err) {
      if (err instanceof ApiRequestError && err.details) setFieldErrors(Object.fromEntries(err.details.map((d) => [d.path, d.message])));
      else setFormError(errorText(t, err));
    } finally {
      setBusy(false);
    }
  }

  const field = (name: keyof MyBusiness & string, label: string, props: React.InputHTMLAttributes<HTMLInputElement> = {}) => (
    <div>
      <Label htmlFor={`${base}-${name}`}>{label}</Label>
      <Input
        id={`${base}-${name}`}
        name={name}
        defaultValue={(business?.[name] as string | null) ?? ""}
        aria-invalid={!!fieldErrors[name] || undefined}
        aria-describedby={fieldErrors[name] ? `${base}-${name}-err` : undefined}
        {...props}
      />
      <FieldError id={`${base}-${name}-err`}>{fieldErrors[name]}</FieldError>
    </div>
  );

  return (
    <form onSubmit={submit} noValidate className="space-y-5">
      {field("legalName", t.legalName, { required: true, maxLength: 200, autoComplete: "organization" })}
      {field("tradeName", t.tradeName, { maxLength: 200 })}
      <div>
        <Label htmlFor={`${base}-legalForm`}>{t.legalForm}</Label>
        <Select id={`${base}-legalForm`} name="legalForm" defaultValue={business?.legalForm ?? "SOLE_PROPRIETORSHIP"}>
          {Object.entries(t.legalForms).map(([v, l]) => (
            <option key={v} value={v}>
              {l}
            </option>
          ))}
        </Select>
      </div>
      <div className="grid gap-5 sm:grid-cols-2">
        {field("registrationNumber", t.registrationNumber, { maxLength: 64, spellCheck: false })}
        {field("tinNumber", t.tinNumber, { inputMode: "numeric", maxLength: 10, pattern: "\\d{10}" })}
        {field("region", t.region, { maxLength: 80 })}
        {field("city", t.city, { maxLength: 80 })}
      </div>
      {formError ? <Alert tone="error">{formError}</Alert> : null}
      {saved ? <Alert tone="success">{t.saved}</Alert> : null}
      <Button type="submit" disabled={busy}>
        {t.save}
      </Button>
    </form>
  );
}

export function ConsentButton({ t }: { t: T }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  return (
    <Button
      type="button"
      disabled={busy}
      onClick={async () => {
        setBusy(true);
        try {
          await apiSend("POST", "/consents", { purpose: "BUSINESS_DOCUMENT_PROCESSING" });
          router.refresh();
        } finally {
          setBusy(false);
        }
      }}
    >
      {t.consentGive}
    </Button>
  );
}

export function DocumentUploadForm({ t }: { t: T }) {
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
        const formEl = e.currentTarget;
        setError(null);
        const form = new FormData(formEl);
        const file = form.get("file");
        if (!(file instanceof File) || file.size === 0) {
          setError(t.errors.file_required);
          return;
        }
        setBusy(true);
        try {
          await apiUpload("/businesses/documents", form);
          formEl.reset();
          router.refresh();
        } catch (err) {
          setError(errorText(t, err));
        } finally {
          setBusy(false);
        }
      }}
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <Label htmlFor={`${base}-type`}>{t.docType}</Label>
          <Select id={`${base}-type`} name="type" defaultValue="BUSINESS_LICENSE">
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
            accept="application/pdf,image/jpeg,image/png,.pdf,.jpg,.jpeg,.png"
            aria-invalid={!!error || undefined}
            aria-describedby={error ? `${base}-err` : undefined}
            className="block min-h-11 w-full text-sm file:me-3 file:min-h-11 file:rounded-md file:border file:border-line file:bg-white file:px-3 file:font-semibold"
          />
        </div>
      </div>
      <FieldError id={`${base}-err`}>{error}</FieldError>
      <Button type="submit" variant="outline" disabled={busy}>
        {t.upload}
      </Button>
    </form>
  );
}

export function RemoveDocumentButton({ t, id }: { t: T; id: string }) {
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
          await apiSend("DELETE", `/businesses/me/documents/${id}`);
          router.refresh();
        } finally {
          setBusy(false);
        }
      }}
    >
      {t.remove}
    </Button>
  );
}

export function SubmitBusinessButton({ t }: { t: T }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <div className="space-y-3">
      {error ? <Alert tone="error">{error}</Alert> : null}
      <Button
        type="button"
        size="lg"
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          setError(null);
          try {
            await apiSend("POST", "/businesses/me/submit");
            router.refresh();
          } catch (err) {
            setError(errorText(t, err));
          } finally {
            setBusy(false);
          }
        }}
      >
        {t.submit}
      </Button>
    </div>
  );
}
