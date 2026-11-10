import { z } from "zod";

/**
 * Backtest request. Parameter overrides mirror the engine's allow-list and
 * ranges (engine/src/nexus_engine/backtest/jobs.py `UserParams`); the engine
 * validates again before running. Unknown keys are rejected.
 */
export const BacktestParamsSchema = z
  .object({
    min_net_rr: z.number().min(1).max(5),
    stop_atr: z.number().min(0.5).max(4),
    rsi_low: z.number().min(20).max(50),
    rsi_high: z.number().min(50).max(80),
    min_volume_ratio: z.number().min(0).max(3),
    entry_window_bars: z.number().int().min(1).max(24),
    horizon_bars: z.number().int().min(4).max(200),
  })
  .partial()
  .strict();

const FIVE_YEARS_MS = (5 * 365 + 2) * 86_400_000;

export const BacktestRequestSchema = z
  .object({
    pairId: z.number().int().positive(),
    timeframe: z.enum(["15m", "1h", "4h", "1d"]),
    start: z.iso.datetime({ offset: true }),
    end: z.iso.datetime({ offset: true }),
    params: BacktestParamsSchema.default({}),
  })
  .refine((v) => Date.parse(v.end) > Date.parse(v.start), { path: ["end"], message: "End must be after start." })
  .refine((v) => Date.parse(v.end) - Date.parse(v.start) <= FIVE_YEARS_MS, { path: ["start"], message: "The period can be at most 5 years." })
  .refine((v) => Date.parse(v.end) <= Date.now(), { path: ["end"], message: "The period must end in the past." });

export type BacktestRequest = z.infer<typeof BacktestRequestSchema>;
