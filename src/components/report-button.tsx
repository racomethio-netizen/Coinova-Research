"use client";

import { useId, useState } from "react";
import { Button, Field, inputClass, Notice } from "@/components/ui";
import { api } from "@/lib/client/api";

const REASONS = [
  ["misleading", "Misleading or inaccurate"],
  ["spam", "Spam"],
  ["abusive", "Abusive content"],
  ["copyright", "Copyright"],
  ["security", "Security issue"],
  ["other", "Other"],
] as const;

/** Lets a signed-in user report content to the moderation team. */
export function ReportButton({ targetType, targetId }: { targetType: "article" | "signal" | "user" | "other"; targetId?: string }) {
  const uid = useId();
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState<string>("misleading");
  const [details, setDetails] = useState("");
  const [state, setState] = useState<{ pending?: boolean; ok?: boolean; message?: string }>({});

  if (state.ok) return <p className="text-xs text-muted" role="status">{state.message}</p>;
  if (!open) {
    return (
      <Button size="sm" variant="ghost" onClick={() => setOpen(true)} aria-expanded={false}>
        Report a problem
      </Button>
    );
  }
  return (
    <form
      className="grid max-w-md gap-3 rounded-md border border-line p-4"
      onSubmit={async (e) => {
        e.preventDefault();
        setState({ pending: true });
        const res = await api("/api/reports", { method: "POST", body: { targetType, targetId, reason, details: details || undefined } });
        setState(res.ok ? { ok: true, message: "Thanks. Your report was sent to our team." } : { message: res.error.message });
      }}
    >
      {state.message ? <Notice tone="negative">{state.message}</Notice> : null}
      <Field id={`${uid}-reason`} label="Reason">
        <select id={`${uid}-reason`} value={reason} onChange={(e) => setReason(e.target.value)} className={inputClass}>
          {REASONS.map(([v, l]) => (
            <option key={v} value={v}>{l}</option>
          ))}
        </select>
      </Field>
      <Field id={`${uid}-details`} label="Details (optional)" hint="Up to 2,000 characters.">
        <textarea id={`${uid}-details`} value={details} maxLength={2000} rows={3} onChange={(e) => setDetails(e.target.value)} className={inputClass} />
      </Field>
      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={state.pending}>{state.pending ? "Sending…" : "Send report"}</Button>
        <Button type="button" size="sm" variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
      </div>
    </form>
  );
}
