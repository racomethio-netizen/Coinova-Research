"use client";

import { useRouter } from "next/navigation";
import { useId, useState } from "react";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiRequestError, apiSend } from "@/lib/client-api";

export type Field =
  | { name: string; label: string; type: "text" | "textarea"; required?: boolean; placeholder?: string; defaultValue?: string }
  | { name: string; label: string; type: "select"; options: { value: string; label: string }[]; defaultValue?: string }
  | { name: string; label: string; type: "checkbox" };

/**
 * Generic admin action: renders labelled fields, sends JSON to the API (CSRF handled by
 * apiSend), shows the API's message on failure, refreshes server data on success.
 */
export function ActionForm({
  method = "POST",
  path,
  fields = [],
  submitLabel,
  variant = "default",
  confirm,
  successText,
  redirectTo,
  testId,
  extra,
}: {
  method?: "POST" | "PATCH" | "DELETE";
  path: string;
  fields?: Field[];
  submitLabel: string;
  variant?: "default" | "outline" | "navy" | "ghost";
  confirm?: string;
  successText?: string;
  redirectTo?: string;
  testId?: string;
  /** Fixed values sent with the form (e.g. {"isActive": false}). */
  extra?: Record<string, unknown>;
}) {
  const router = useRouter();
  const base = useId();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  return (
    <form
      data-testid={testId}
      className="space-y-3"
      onSubmit={async (e) => {
        e.preventDefault();
        if (confirm && !window.confirm(confirm)) return;
        const form = new FormData(e.currentTarget);
        const body: Record<string, unknown> = { ...(extra ?? {}) };
        for (const f of fields) {
          if (f.type === "checkbox") body[f.name] = form.get(f.name) === "on";
          else {
            const v = String(form.get(f.name) ?? "");
            if (v !== "") body[f.name] = v;
          }
        }
        setBusy(true);
        setError(null);
        setDone(false);
        try {
          await apiSend(method, path, fields.length || extra ? body : undefined);
          setDone(true);
          if (redirectTo) router.push(redirectTo);
          router.refresh();
        } catch (err) {
          setError(err instanceof ApiRequestError ? err.message : "Request failed");
        } finally {
          setBusy(false);
        }
      }}
    >
      {fields.map((f) => {
        const id = `${base}-${f.name}`;
        if (f.type === "checkbox") {
          return (
            <div key={f.name} className="flex items-center gap-2">
              <input id={id} name={f.name} type="checkbox" className="size-5 accent-emerald-700" />
              <label htmlFor={id} className="text-sm">{f.label}</label>
            </div>
          );
        }
        return (
          <div key={f.name}>
            <Label htmlFor={id}>{f.label}</Label>
            {f.type === "select" ? (
              <Select id={id} name={f.name} defaultValue={f.defaultValue}>
                {f.options.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </Select>
            ) : f.type === "textarea" ? (
              <textarea
                id={id}
                name={f.name}
                required={f.required}
                placeholder={f.placeholder}
                defaultValue={f.defaultValue}
                rows={3}
                className="block w-full rounded-md border border-line bg-white px-3 py-2 text-base focus-visible:border-emerald-600 focus-visible:outline-2 focus-visible:outline-emerald-600"
              />
            ) : (
              <Input id={id} name={f.name} required={f.required} placeholder={f.placeholder} defaultValue={f.defaultValue} />
            )}
          </div>
        );
      })}
      {error ? <Alert tone="error">{error}</Alert> : null}
      {done && successText ? <Alert tone="success">{successText}</Alert> : null}
      <Button type="submit" variant={variant} size="sm" disabled={busy}>
        {submitLabel}
      </Button>
    </form>
  );
}
