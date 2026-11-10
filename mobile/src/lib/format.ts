/** Display formatting for decimal strings (no float arithmetic on money). */
export function fmtDecimal(v: string | null | undefined, maxFraction = 8): string {
  if (v === null || v === undefined || v === "") return "—";
  const m = /^(-?)(\d+)(?:\.(\d+))?$/.exec(v.trim());
  if (!m) return "—";
  const [, sign, int, frac = ""] = m;
  const grouped = int!.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const f = frac.slice(0, maxFraction).replace(/0+$/, "");
  return `${sign}${grouped}${f ? `.${f}` : ""}`;
}

export function fmtPctFromFraction(v: string | null | undefined, digits = 1): string {
  if (v === null || v === undefined) return "—";
  const n = Number(v);
  return Number.isFinite(n) ? `${(n * 100).toFixed(digits)}%` : "—";
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : `${d.toISOString().slice(0, 16).replace("T", " ")} UTC`;
}

export const OUTCOME_LABEL: Record<string, string> = {
  win: "Target hit",
  loss: "Stopped out",
  expired_closed: "Closed at horizon",
  expired_no_entry: "Expired, no entry",
  invalidated: "Invalidated",
};
