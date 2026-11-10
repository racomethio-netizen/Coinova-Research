import Decimal from "decimal.js";
import { z } from "zod";

/** Decimal strings only (no floats). Quantities: 12 dp; prices: 8 dp — matching the database columns. */
const dec = (field: string, places: number, max: string) =>
  z
    .string()
    .trim()
    .regex(new RegExp(`^\\d{1,12}(\\.\\d{1,${places}})?$`), `${field} must be a positive decimal with at most ${places} decimal places.`)
    .refine((v) => new Decimal(v).gt(0), `${field} must be greater than zero.`)
    .refine((v) => new Decimal(v).lte(max), `${field} is too large.`);

const price = (field: string) => dec(field, 8, "1000000000");

export const PaperOrderSchema = z
  .object({
    pairId: z.number().int().positive(),
    side: z.enum(["buy", "sell"]),
    type: z.enum(["market", "limit"]),
    quantity: dec("Quantity", 12, "1000000"),
    limitPrice: price("Limit price").optional(),
    stopLoss: price("Stop loss").optional(),
    takeProfit: price("Take profit").optional(),
    signalId: z.uuid().optional(),
  })
  .strict()
  .refine((v) => (v.type === "limit") === (v.limitPrice !== undefined), { path: ["limitPrice"], message: "Limit orders need a limit price; market orders must not have one." })
  .refine((v) => v.side === "buy" || (v.stopLoss === undefined && v.takeProfit === undefined), {
    path: ["stopLoss"],
    message: "Stop loss and take profit apply to buy orders (long-only paper trading).",
  })
  .refine((v) => !v.stopLoss || !v.takeProfit || new Decimal(v.stopLoss).lt(v.takeProfit), { path: ["takeProfit"], message: "Take profit must be above the stop loss." })
  .refine((v) => !v.limitPrice || !v.stopLoss || new Decimal(v.stopLoss).lt(v.limitPrice), { path: ["stopLoss"], message: "Stop loss must be below the limit price." })
  .refine((v) => !v.limitPrice || !v.takeProfit || new Decimal(v.takeProfit).gt(v.limitPrice), { path: ["takeProfit"], message: "Take profit must be above the limit price." });

export const PaperAccountActionSchema = z
  .object({
    action: z.enum(["open", "reset"]),
    startingBalance: dec("Starting balance", 2, "10000000").refine((v) => new Decimal(v).gte(100), "Starting balance must be at least 100.").default("10000"),
  })
  .strict();

export const PaperProtectionSchema = z
  .object({ stopLoss: price("Stop loss").nullable(), takeProfit: price("Take profit").nullable() })
  .strict()
  .refine((v) => !v.stopLoss || !v.takeProfit || new Decimal(v.stopLoss).lt(v.takeProfit), { path: ["takeProfit"], message: "Take profit must be above the stop loss." });
