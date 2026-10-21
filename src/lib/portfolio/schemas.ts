import { z } from "zod";

/** Up to 20 integer digits and 18 fractional digits — matches NUMERIC(38,18). */
const DECIMAL = /^\d{1,20}(\.\d{1,18})?$/;

export const decimalString = (field: string) =>
  z
    .union([z.string(), z.number()])
    .transform((v) => (typeof v === "number" ? (Number.isFinite(v) ? String(v) : "invalid") : v.trim()))
    .refine((v) => DECIMAL.test(v), { message: `${field} must be a non-negative decimal with at most 18 decimal places.` });

const positiveDecimal = (field: string) => decimalString(field).refine((v) => Number(v) > 0, { message: `${field} must be greater than zero.` });

export const coinIdSchema = z.string().regex(/^[a-z0-9][a-z0-9-]{0,99}$/, "Invalid coin identifier.");

const MAX_FUTURE_MS = 5 * 60_000;
const MIN_DATE = Date.UTC(2009, 0, 3);

export const occurredAtSchema = z.iso
  .datetime({ offset: true })
  .refine((v) => Date.parse(v) <= Date.now() + MAX_FUTURE_MS, { message: "Transaction date cannot be in the future." })
  .refine((v) => Date.parse(v) >= MIN_DATE, { message: "Transaction date is too early." });

export const TransactionInputSchema = z
  .object({
    portfolioId: z.uuid(),
    coinId: coinIdSchema,
    type: z.enum(["buy", "sell", "transfer_in", "transfer_out"]),
    quantity: positiveDecimal("Quantity"),
    priceUsd: decimalString("Price").nullable().optional(),
    feeUsd: decimalString("Fee").optional().default("0"),
    occurredAt: occurredAtSchema,
    notes: z.string().trim().max(500).optional().nullable(),
  })
  .superRefine((v, ctx) => {
    const needsPrice = v.type === "buy" || v.type === "sell" || v.type === "transfer_in";
    if (needsPrice && (v.priceUsd === null || v.priceUsd === undefined)) {
      ctx.addIssue({ code: "custom", path: ["priceUsd"], message: v.type === "transfer_in" ? "Cost basis per unit is required for transfers in (use 0 for airdrops)." : "Price is required." });
    }
  });
export type TransactionInput = z.infer<typeof TransactionInputSchema>;

export const TransactionPatchSchema = z
  .object({
    type: z.enum(["buy", "sell", "transfer_in", "transfer_out"]).optional(),
    quantity: positiveDecimal("Quantity").optional(),
    priceUsd: decimalString("Price").nullable().optional(),
    feeUsd: decimalString("Fee").optional(),
    occurredAt: occurredAtSchema.optional(),
    notes: z.string().trim().max(500).nullable().optional(),
  })
  .refine((v) => Object.keys(v).length > 0, { message: "No fields to update." });

export const PortfolioInputSchema = z.object({
  name: z.string().trim().min(1, "Name is required.").max(60),
});
