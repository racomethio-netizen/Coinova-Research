import { PaperSummarySchema, PerformanceResponseSchema, ScannerRowSchema, SignalSummarySchema } from "@nexus/shared";
import { describe, expect, it } from "vitest";
import { valueAccount } from "@/lib/paper/summary";
import { toPerformanceRow, toScannerRow } from "@/server/nexus";

/** The server's mappers must produce exactly the shapes the mobile client parses. */
describe("API contract (packages/shared)", () => {
  it("scanner rows", () => {
    const row = toScannerRow({ pair_id: 1, symbol: "BTCUSDT", timeframe: "1h", as_of: "2026-01-01T01:00:00+00:00", close: "100.5", ema20: "99", ema50: "98", ema200: "90",
      rsi14: "55.2", macd_hist: "-0.4", atr14: "1.2", volume_ratio: "1.1", htf: "4h", htf_ema50: "95", htf_ema200: "91" });
    expect(ScannerRowSchema.parse(row)).toMatchObject({ htfTrendUp: true, macdHist: "-0.4" });
  });

  it("performance rows hide rates below the minimum sample", () => {
    const small = toPerformanceRow({ pair_id: 1, timeframe: "1h", signals: 5, closed: 4, wins: 2, losses: 1, expired_closed: 0, expired_no_entry: 1, invalidated: 0, avg_r_multiple: "0.5", market_pairs: { symbol: "BTCUSDT" } });
    const big = toPerformanceRow({ pair_id: 1, timeframe: "1h", signals: 150, closed: 140, wins: 60, losses: 50, expired_closed: 10, expired_no_entry: 20, invalidated: 0, avg_r_multiple: "0.12", market_pairs: { symbol: "BTCUSDT" } });
    const parsed = PerformanceResponseSchema.parse({ minSample: 100, rows: [small, big] });
    expect(parsed.rows[0]).toMatchObject({ insufficientSample: true, winRate: null, avgRMultiple: null, entered: 3 });
    expect(parsed.rows[1]).toMatchObject({ insufficientSample: false, winRate: "0.500000", avgRMultiple: "0.1200", entered: 120 });
  });

  it("paper summaries", () => {
    const valued = valueAccount("100", [], [], 0);
    expect(PaperSummarySchema.parse({ virtual: true, account: { id: "8a6b7c1e-4a8f-4f5a-9b3c-1d2e3f4a5b6c", name: "Paper account", starting_balance: "100.00000000", ...valued }, orders: [], fills: [], equityHistory: [] }).account?.equity).toBe("100.00000000");
    expect(PaperSummarySchema.parse({ virtual: true, account: null }).account).toBeNull();
  });

  it("rejects malformed signals (e.g. float prices or unknown visibility)", () => {
    const ok = { id: "8a6b7c1e-4a8f-4f5a-9b3c-1d2e3f4a5b6c", pairId: 1, symbol: "BTCUSDT", timeframe: "1h", direction: "BUY", asOf: "2026-01-01T00:00:00Z", publishedAt: "2026-01-01T00:00:20Z",
      entryLow: "99.5", entryHigh: "100.1", stopLoss: "97", takeProfits: ["104"], riskReward: "1.8", confidenceBand: "not_calibrated", visibility: "staff", outcome: null };
    expect(SignalSummarySchema.safeParse(ok).success).toBe(true);
    expect(SignalSummarySchema.safeParse({ ...ok, entryLow: 99.5 }).success).toBe(false);
    expect(SignalSummarySchema.safeParse({ ...ok, visibility: "everyone" }).success).toBe(false);
  });
});
