import { describe, expect, it } from "vitest";
import { BacktestRequestSchema } from "@/lib/backtests/schema";

const base = { pairId: 1, timeframe: "1h", start: "2024-01-01T00:00:00Z", end: "2024-06-01T00:00:00Z" };

describe("backtest request validation", () => {
  it("accepts bounded parameter overrides", () => {
    const r = BacktestRequestSchema.safeParse({ ...base, params: { min_net_rr: 2, horizon_bars: 48 } });
    expect(r.success).toBe(true);
  });

  it("rejects unknown keys (no arbitrary strategy code or settings)", () => {
    expect(BacktestRequestSchema.safeParse({ ...base, params: { __proto__: 1, code: "import os" } }).success).toBe(false);
    expect(BacktestRequestSchema.safeParse({ ...base, params: { max_drawdown_halt: 0.9 } }).success).toBe(false);
  });

  it("rejects out-of-range values and bad periods", () => {
    expect(BacktestRequestSchema.safeParse({ ...base, params: { min_net_rr: 0.5 } }).success).toBe(false);
    expect(BacktestRequestSchema.safeParse({ ...base, params: { horizon_bars: 2.5 } }).success).toBe(false);
    expect(BacktestRequestSchema.safeParse({ ...base, end: base.start }).success).toBe(false);
    expect(BacktestRequestSchema.safeParse({ ...base, start: "2017-01-01T00:00:00Z" }).success).toBe(false);
    expect(BacktestRequestSchema.safeParse({ ...base, end: new Date(Date.now() + 86_400_000).toISOString() }).success).toBe(false);
    expect(BacktestRequestSchema.safeParse({ ...base, timeframe: "5m" }).success).toBe(false);
  });
});
