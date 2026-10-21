/**
 * Display formatting. Missing values render as an em dash, never as zero.
 */
const DASH = "—";

export function fmtUsd(v: number | null | undefined, opts: { compact?: boolean } = {}): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return DASH;
  if (opts.compact && Math.abs(v) >= 1_000_000) {
    return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 2 }).format(v);
  }
  const abs = Math.abs(v);
  const digits = abs === 0 ? 2 : abs >= 1 ? 2 : abs >= 0.01 ? 4 : 8;
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: digits }).format(v);
}

export function fmtNumber(v: number | null | undefined, opts: { compact?: boolean; maxDigits?: number } = {}): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return DASH;
  return new Intl.NumberFormat("en-US", {
    notation: opts.compact ? "compact" : "standard",
    maximumFractionDigits: opts.maxDigits ?? (opts.compact ? 2 : 0),
  }).format(v);
}

export function fmtPct(v: number | null | undefined, opts: { signed?: boolean; digits?: number } = {}): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return DASH;
  const s = v.toFixed(opts.digits ?? 2);
  return `${opts.signed && v > 0 ? "+" : ""}${s}%`;
}

export function fmtDate(iso: string | null | undefined, opts: { time?: boolean } = {}): string {
  if (!iso) return DASH;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return DASH;
  return new Intl.DateTimeFormat("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    ...(opts.time ? { hour: "2-digit", minute: "2-digit", timeZone: "UTC", timeZoneName: "short" } : { timeZone: "UTC" }),
  }).format(d);
}

export function fmtRelative(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return DASH;
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return DASH;
  const s = Math.round((now - t) / 1000);
  if (s < 0) return "just now";
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

/** Decimal strings from the database, formatted for display. */
export function fmtDecimal(v: string | null | undefined, maxDigits = 8): string {
  if (v === null || v === undefined || v === "") return DASH;
  const n = Number(v);
  if (!Number.isFinite(n)) return v;
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: maxDigits }).format(n);
}
