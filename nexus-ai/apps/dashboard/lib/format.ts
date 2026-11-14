// Display formatting only. Values arrive as exact decimal strings from the API; missing
// values render as an em dash, never as zero.

export const DASH = "—";

export function num(v: string | number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || v === "") return DASH;
  const n = typeof v === "number" ? v : Number(v);
  if (!Number.isFinite(n)) return DASH;
  return n.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function qty(v: string | null | undefined): string {
  if (v === null || v === undefined) return DASH;
  const n = Number(v);
  if (!Number.isFinite(n)) return DASH;
  return n.toLocaleString("en-US", { maximumFractionDigits: 8 });
}

export function pct(v: string | number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || v === "") return DASH;
  const n = typeof v === "number" ? v : Number(v);
  if (!Number.isFinite(n)) return DASH;
  return `${(n * 100).toFixed(digits)}%`;
}

export function time(v: string | null | undefined): string {
  if (!v) return DASH;
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return DASH;
  return d.toISOString().replace("T", " ").slice(0, 19) + " UTC";
}

export function signed(v: string | null | undefined): "up" | "down" | "flat" {
  if (v === null || v === undefined) return "flat";
  const n = Number(v);
  return n > 0 ? "up" : n < 0 ? "down" : "flat";
}
