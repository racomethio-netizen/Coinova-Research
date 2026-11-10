"use client";

import { useId, useState } from "react";
import { Button, Field, inputClass } from "@/components/ui";
import { useMutation } from "@/lib/client/use-mutation";

/** One-click admin action (PATCH/POST JSON). Errors from database rules are shown verbatim (they contain no secrets). */
export function ActionButton({ url, method = "PATCH", body, label, confirm, variant = "secondary" }: {
  url: string; method?: string; body: unknown; label: string; confirm?: string; variant?: "primary" | "secondary" | "ghost";
}) {
  const m = useMutation();
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <Button size="sm" variant={variant} disabled={m.pending} onClick={() => { if (confirm && !window.confirm(confirm)) return; void m.run(url, method, body); }}>{label}</Button>
      {m.error ? <span className="text-xs text-negative" role="alert">{m.error}</span> : null}
    </span>
  );
}

export function AnnotateForm({ signalId }: { signalId: string }) {
  const m = useMutation();
  const uid = useId();
  const [kind, setKind] = useState("note");
  const [body, setBody] = useState("");
  return (
    <form className="grid gap-2 sm:grid-cols-[140px_1fr_auto]" onSubmit={(e) => { e.preventDefault(); void m.run(`/api/admin/signals/${signalId}/annotations`, "POST", { kind, body }, () => setBody("")); }}>
      <label className="sr-only" htmlFor={`${uid}-k`}>Kind</label>
      <select id={`${uid}-k`} value={kind} onChange={(e) => setKind(e.target.value)} className={inputClass}><option value="note">Note</option><option value="correction">Correction</option><option value="retraction">Retraction</option></select>
      <label className="sr-only" htmlFor={`${uid}-b`}>Annotation text (public)</label>
      <input id={`${uid}-b`} value={body} onChange={(e) => setBody(e.target.value)} placeholder="Public annotation (10+ characters)" className={inputClass} aria-invalid={m.fields.body ? true : undefined} />
      <Button size="sm" type="submit" disabled={m.pending}>Add</Button>
      {m.error ? <p className="text-xs text-negative sm:col-span-3" role="alert">{m.error}</p> : null}
    </form>
  );
}

export function TermsReviewForm({ sourceKey }: { sourceKey: string }) {
  const m = useMutation();
  const uid = useId();
  const [notes, setNotes] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  return (
    <form className="space-y-2" onSubmit={(e) => { e.preventDefault(); void m.run(`/api/admin/sources/${sourceKey}`, "POST", { notes, confirm: confirmed }); }}>
      <Field id={`${uid}-n`} label="Review notes (permitted use, regions, evidence)" error={m.fields.notes}>
        <textarea id={`${uid}-n`} rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} className={inputClass} aria-invalid={m.fields.notes ? true : undefined} />
      </Field>
      <label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} /> I confirm the terms were reviewed for the intended use. This enables production ingestion from this source (gate G-4).</label>
      <Button size="sm" type="submit" disabled={m.pending || !confirmed}>Record review</Button>
      {m.error ? <p className="text-xs text-negative" role="alert">{m.error}</p> : null}
    </form>
  );
}
