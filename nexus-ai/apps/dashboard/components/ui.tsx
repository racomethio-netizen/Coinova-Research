"use client";

import type { ReactNode } from "react";
import { DASH } from "@/lib/format";

export function Card({ title, children, actions }: { title?: string; children: ReactNode; actions?: ReactNode }) {
  return (
    <section className="rounded-xl border border-line bg-panel p-4">
      {(title || actions) && (
        <div className="mb-3 flex items-center justify-between gap-2">
          {title && <h2 className="text-sm font-semibold tracking-wide text-muted uppercase">{title}</h2>}
          {actions}
        </div>
      )}
      {children}
    </section>
  );
}

export function Stat({ label, value, tone, hint }: { label: string; value: ReactNode; tone?: "up" | "down" | "flat" | "warn"; hint?: string }) {
  const color = tone === "up" ? "text-up" : tone === "down" ? "text-down" : tone === "warn" ? "text-warn" : "text-text";
  return (
    <div className="rounded-xl border border-line bg-panel p-4">
      <div className="text-xs text-muted">{label}</div>
      <div className={`num mt-1 text-xl font-semibold ${color}`}>{value}</div>
      {hint && <div className="mt-1 text-xs text-muted">{hint}</div>}
    </div>
  );
}

const BADGE: Record<string, string> = {
  ok: "bg-up/15 text-up", good: "bg-up/15 text-up", bad: "bg-down/15 text-down",
  warn: "bg-warn/15 text-warn", info: "bg-accent/15 text-accent", muted: "bg-elevated text-muted",
};

export function Badge({ tone = "muted", children }: { tone?: keyof typeof BADGE; children: ReactNode }) {
  return <span className={`inline-flex items-center rounded px-2 py-0.5 text-xs font-medium ${BADGE[tone]}`}>{children}</span>;
}

export function statusTone(s: string | null | undefined): keyof typeof BADGE {
  if (!s) return "muted";
  if (["filled", "executed", "open", "validated", "active", "ok", "passed"].includes(s)) return "good";
  if (["rejected", "unknown", "failed", "critical", "error", "down"].includes(s)) return "bad";
  if (["partially_filled", "warning", "pending_submit", "submitted", "new", "closing"].includes(s)) return "warn";
  return "muted";
}

export type Column<T> = { key: string; label: string; render?: (row: T) => ReactNode; numeric?: boolean };

export function Table<T extends Record<string, unknown>>({ rows, columns, empty }: { rows: T[] | null | undefined; columns: Column<T>[]; empty: string }) {
  if (!rows || rows.length === 0) return <p className="py-6 text-center text-sm text-muted">{empty}</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-max text-sm">
        <thead>
          <tr className="border-b border-line text-left text-xs text-muted">
            {columns.map((c) => (
              <th key={c.key} scope="col" className={`px-2 py-2 font-medium ${c.numeric ? "text-right" : ""}`}>{c.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-b border-line/50 hover:bg-elevated/60">
              {columns.map((c) => (
                <td key={c.key} className={`px-2 py-2 ${c.numeric ? "num text-right" : ""}`}>
                  {c.render ? c.render(r) : ((r[c.key] as ReactNode) ?? DASH)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Loading({ error, loading }: { error: string | null; loading: boolean }) {
  if (error) return <p role="alert" className="rounded-lg border border-down/40 bg-down/10 p-3 text-sm text-down">Could not load: {error}</p>;
  if (loading) return <p className="text-sm text-muted">Loading…</p>;
  return null;
}

export function PageHeader({ title, subtitle, children }: { title: string; subtitle?: string; children?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-2xl font-semibold">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-muted">{subtitle}</p>}
      </div>
      {children}
    </div>
  );
}

export function Button({ children, onClick, tone = "default", disabled, type = "button" }: { children: ReactNode; onClick?: () => void; tone?: "default" | "danger" | "primary"; disabled?: boolean; type?: "button" | "submit" }) {
  const cls = tone === "danger" ? "bg-down text-white hover:bg-down/90" : tone === "primary" ? "bg-accent text-white hover:bg-accent/90" : "bg-elevated text-text hover:bg-line";
  return (
    <button type={type} disabled={disabled} onClick={onClick} className={`rounded-lg px-3 py-2 text-sm font-medium disabled:opacity-50 ${cls}`}>
      {children}
    </button>
  );
}

export function Field({ id, label, type = "text", value, onChange, error, autoComplete, inputMode }: { id: string; label: string; type?: string; value: string; onChange: (v: string) => void; error?: string | null; autoComplete?: string; inputMode?: "numeric" | "text" }) {
  return (
    <div>
      <label htmlFor={id} className="mb-1 block text-xs text-muted">{label}</label>
      <input id={id} type={type} value={value} autoComplete={autoComplete} inputMode={inputMode}
        aria-invalid={error ? true : undefined} aria-describedby={error ? `${id}-err` : undefined}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-lg border border-line bg-elevated px-3 py-2 text-sm text-text placeholder:text-muted aria-[invalid=true]:border-down" />
      {error && <p id={`${id}-err`} className="mt-1 text-xs text-down">{error}</p>}
    </div>
  );
}
