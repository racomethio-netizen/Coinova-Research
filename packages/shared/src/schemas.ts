import Decimal from "decimal.js";
import { z } from "zod";

/**
 * The API contract between the web server and its clients (web and mobile).
 * Server routes build these shapes; the mobile client parses every response
 * with them, so a breaking change fails a test instead of a user's app.
 * Money and quantities are decimal strings, never floats.
 */

export const TIMEFRAMES = ["15m", "1h", "4h", "1d"] as const;
export const TimeframeSchema = z.enum(TIMEFRAMES);
const dec = z.string().regex(/^-?\d+(\.\d+)?$/);
const iso = z.string().min(10);

export const PairSchema = z.object({
  id: z.number().int(),
  symbol: z.string(),
  baseAsset: z.string(),
  quoteAsset: z.string(),
  timeframes: z.array(TimeframeSchema),
  source: z.string(),
});

export const ScannerRowSchema = z.object({
  pairId: z.number().int(),
  symbol: z.string(),
  timeframe: TimeframeSchema,
  asOf: iso,
  close: dec,
  ema20: dec,
  ema50: dec,
  ema200: dec,
  rsi14: dec,
  /** Advanced indicators are null without the analytics.advanced entitlement (Pro+). */
  macdHist: dec.nullable(),
  atr14: dec.nullable(),
  volumeRatio: dec.nullable(),
  htf: z.string().nullable(),
  htfTrendUp: z.boolean().nullable(),
});

export const OutcomeSchema = z.object({
  outcome: z.enum(["win", "loss", "expired_closed", "expired_no_entry", "invalidated"]),
  netReturn: dec.nullable(),
  rMultiple: dec.nullable(),
  closedAt: iso,
});

export const SignalSummarySchema = z.object({
  id: z.uuid(),
  pairId: z.number().int(),
  symbol: z.string(),
  timeframe: TimeframeSchema,
  direction: z.enum(["BUY", "SELL"]),
  asOf: iso,
  publishedAt: iso,
  entryLow: dec,
  entryHigh: dec,
  stopLoss: dec,
  takeProfits: z.array(dec).min(1).max(3),
  riskReward: dec,
  confidenceBand: z.enum(["not_calibrated", "low", "medium", "high"]),
  visibility: z.enum(["staff", "beta", "public"]),
  outcome: OutcomeSchema.nullable(),
});

export const SignalDetailSchema = SignalSummarySchema.extend({
  explanation: z.object({
    summary: z.string().optional(),
    reasons: z.array(z.object({ code: z.string(), detail: z.string() })).optional(),
    risks: z.array(z.string()).optional(),
    invalidation: z.string().optional(),
    confidence: z.string().optional(),
  }).loose(),
  assumptions: z.record(z.string(), z.string()),
  entryExpiresAt: iso,
  horizonBars: z.number().int(),
  contentHash: z.string().regex(/^[0-9a-f]{64}$/),
  prevHash: z.string().regex(/^[0-9a-f]{64}$/).nullable(),
  strategy: z.object({ key: z.string(), version: z.string(), name: z.string() }).nullable(),
  events: z.array(z.object({ event_type: z.string(), occurred_at: iso, price: z.union([dec, z.number()]).nullable() })),
  annotations: z.array(z.object({ kind: z.enum(["note", "correction", "retraction"]), body: z.string(), created_at: iso })),
});

export const SignalsResponseSchema = z.object({
  gate: z.enum(["staff", "beta", "public"]),
  signals: z.array(SignalSummarySchema),
});

export const PerformanceRowSchema = z.object({
  pairId: z.number().int(),
  symbol: z.string(),
  timeframe: TimeframeSchema,
  signals: z.number().int(),
  closed: z.number().int(),
  wins: z.number().int(),
  losses: z.number().int(),
  expiredClosed: z.number().int(),
  expiredNoEntry: z.number().int(),
  invalidated: z.number().int(),
  entered: z.number().int(),
  insufficientSample: z.boolean(),
  winRate: dec.nullable(),
  avgRMultiple: dec.nullable(),
  firstPublishedAt: iso.nullable(),
  lastPublishedAt: iso.nullable(),
});
export const PerformanceResponseSchema = z.object({ minSample: z.number().int(), rows: z.array(PerformanceRowSchema) });

export const ValuedPositionSchema = z.object({
  pairId: z.number().int(),
  symbol: z.string(),
  quantity: dec,
  costBasis: dec,
  realizedPnl: dec,
  stopLoss: dec.nullable(),
  takeProfit: dec.nullable(),
  avgEntry: dec.nullable(),
  price: dec.nullable(),
  priceStale: z.boolean(),
  marketValue: dec.nullable(),
  unrealizedPnl: dec.nullable(),
});

export const PaperSummarySchema = z.object({
  virtual: z.literal(true),
  account: z
    .object({
      id: z.uuid(),
      name: z.string(),
      starting_balance: z.union([dec, z.number()]),
      cash: dec,
      equity: dec.nullable(),
      realizedPnl: dec,
      positions: z.array(ValuedPositionSchema),
    })
    .loose()
    .nullable(),
  orders: z.array(z.object({ id: z.uuid(), side: z.enum(["buy", "sell"]), order_type: z.enum(["market", "limit"]), status: z.string() }).loose()).optional(),
  fills: z.array(z.object({ id: z.number().int(), side: z.enum(["buy", "sell"]), reason: z.string() }).loose()).optional(),
  equityHistory: z.array(z.object({ taken_at: iso }).loose()).optional(),
});

export const MembershipSchema = z.object({ tier: z.enum(["free", "pro", "premium"]) }).loose();

// ------------------------------------------------------------------ requests

/** Decimal strings only (no floats). Quantities: 12 dp; prices: 8 dp — matching the database columns. */
const decIn = (field: string, places: number, max: string) =>
  z
    .string()
    .trim()
    .regex(new RegExp(`^\\d{1,12}(\\.\\d{1,${places}})?$`), `${field} must be a positive decimal with at most ${places} decimal places.`)
    .refine((v) => new Decimal(v).gt(0), `${field} must be greater than zero.`)
    .refine((v) => new Decimal(v).lte(max), `${field} is too large.`);
const priceIn = (field: string) => decIn(field, 8, "1000000000");

export const PaperOrderSchema = z
  .object({
    pairId: z.number().int().positive(),
    side: z.enum(["buy", "sell"]),
    type: z.enum(["market", "limit"]),
    quantity: decIn("Quantity", 12, "1000000"),
    limitPrice: priceIn("Limit price").optional(),
    stopLoss: priceIn("Stop loss").optional(),
    takeProfit: priceIn("Take profit").optional(),
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
    startingBalance: decIn("Starting balance", 2, "10000000").refine((v) => new Decimal(v).gte(100), "Starting balance must be at least 100.").default("10000"),
  })
  .strict();

export const PaperProtectionSchema = z
  .object({ stopLoss: priceIn("Stop loss").nullable(), takeProfit: priceIn("Take profit").nullable() })
  .strict()
  .refine((v) => !v.stopLoss || !v.takeProfit || new Decimal(v.stopLoss).lt(v.takeProfit), { path: ["takeProfit"], message: "Take profit must be above the stop loss." });

/** Expo push token registration (mobile). */
export const DeviceRegistrationSchema = z
  .object({
    token: z.string().regex(/^Expo(nent)?PushToken\[[A-Za-z0-9_-]{10,200}\]$/, "Invalid Expo push token."),
    platform: z.enum(["ios", "android"]),
  })
  .strict();

export type Pair = z.infer<typeof PairSchema>;
export type ScannerRow = z.infer<typeof ScannerRowSchema>;
export type SignalSummary = z.infer<typeof SignalSummarySchema>;
export type SignalDetail = z.infer<typeof SignalDetailSchema>;
export type PerformanceRow = z.infer<typeof PerformanceRowSchema>;
export type PaperSummary = z.infer<typeof PaperSummarySchema>;
export type PaperOrder = z.infer<typeof PaperOrderSchema>;
