"use client";

import { useRouter } from "next/navigation";
import { useId, useState } from "react";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/input";
import { FieldError, Label } from "@/components/ui/label";
import type { Dictionary, Locale } from "@/i18n/dictionaries";
import { ApiRequestError, apiSend } from "@/lib/client-api";

export function SellerSetupForm({ lang, t, errors }: { lang: Locale; t: Dictionary["setup"]; errors: Dictionary["auth"]["errors"] }) {
  const router = useRouter();
  const base = useId();
  const [busy, setBusy] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    setFieldErrors({});
    setFormError(null);
    const f = new FormData(e.currentTarget);
    const body = Object.fromEntries(["displayName", "displayNameAm", "sellerType", "region", "city"].map((k) => [k, String(f.get(k) ?? "")]));
    try {
      await apiSend("POST", "/sellers", body);
      router.push(`/${lang}/dashboard`);
      router.refresh();
    } catch (err) {
      if (err instanceof ApiRequestError && err.code === "seller_exists") {
        router.push(`/${lang}/dashboard`);
        return;
      }
      if (err instanceof ApiRequestError && err.details) {
        setFieldErrors(Object.fromEntries(err.details.map((d) => [d.path, d.message])));
      } else {
        setFormError(errors.generic);
      }
      setBusy(false);
    }
  }

  const field = (name: string, label: string, props: React.InputHTMLAttributes<HTMLInputElement> = {}) => (
    <div>
      <Label htmlFor={`${base}-${name}`}>{label}</Label>
      <Input
        id={`${base}-${name}`}
        name={name}
        aria-invalid={!!fieldErrors[name] || undefined}
        aria-describedby={fieldErrors[name] ? `${base}-${name}-err` : undefined}
        {...props}
      />
      <FieldError id={`${base}-${name}-err`}>{fieldErrors[name]}</FieldError>
    </div>
  );

  return (
    <form onSubmit={submit} noValidate className="space-y-5">
      {field("displayName", t.displayName, { required: true, minLength: 2, maxLength: 120, autoComplete: "organization" })}
      {field("displayNameAm", t.displayNameAm, { maxLength: 120, lang: "am" })}
      <div>
        <Label htmlFor={`${base}-sellerType`}>{t.sellerType}</Label>
        <Select id={`${base}-sellerType`} name="sellerType" required defaultValue="INDIVIDUAL">
          {Object.entries(t.types).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </Select>
      </div>
      <div className="grid gap-5 sm:grid-cols-2">
        {field("region", t.region, { maxLength: 80, autoComplete: "address-level1" })}
        {field("city", t.city, { maxLength: 80, autoComplete: "address-level2" })}
      </div>
      {formError ? <Alert tone="error">{formError}</Alert> : null}
      <Button type="submit" className="w-full" disabled={busy}>
        {t.submit}
      </Button>
    </form>
  );
}
