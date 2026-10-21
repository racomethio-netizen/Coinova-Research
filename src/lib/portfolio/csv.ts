import type { PortfolioTransaction } from "./calc";

/**
 * RFC 4180 CSV export with formula-injection protection: text cells starting
 * with = + - @ (or tab / CR) are prefixed with a single quote so spreadsheet
 * software does not execute them. Numeric columns are emitted verbatim from
 * validated decimal strings.
 */

const FORMULA_PREFIX = /^[=+\-@\t\r]/;

export function escapeCsvText(value: string): string {
  let v = value;
  if (FORMULA_PREFIX.test(v)) v = `'${v}`;
  if (/[",\n\r]/.test(v)) v = `"${v.replace(/"/g, '""')}"`;
  return v;
}

const NUMERIC = /^-?\d+(\.\d+)?$/;
function numericCell(v: string | null): string {
  if (v === null || v === "") return "";
  return NUMERIC.test(v) ? v : escapeCsvText(v);
}

export interface ExportRow extends PortfolioTransaction {
  portfolioName: string;
  coinSymbol: string | null;
  notes: string | null;
}

export const CSV_HEADER = ["occurred_at", "portfolio", "coin_id", "symbol", "type", "quantity", "price_usd", "fee_usd", "notes", "transaction_id"];

export function transactionsToCsv(rows: readonly ExportRow[]): string {
  const lines = [CSV_HEADER.join(",")];
  for (const r of rows) {
    lines.push(
      [
        escapeCsvText(new Date(r.occurredAt).toISOString()),
        escapeCsvText(r.portfolioName),
        escapeCsvText(r.coinId),
        escapeCsvText(r.coinSymbol ?? ""),
        escapeCsvText(r.type),
        numericCell(r.quantity),
        numericCell(r.priceUsd),
        numericCell(r.feeUsd),
        escapeCsvText(r.notes ?? ""),
        escapeCsvText(r.id),
      ].join(","),
    );
  }
  return lines.join("\r\n") + "\r\n";
}
