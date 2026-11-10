import { describe, expect, it } from "vitest";
import { PaperAccountActionSchema, PaperOrderSchema } from "@/lib/paper/schema";
import { valueAccount } from "@/lib/paper/summary";

describe("paper order validation", () => {
  const base = { pairId: 1, side: "buy", type: "market", quantity: "0.5" };
  it("accepts decimal strings and bracketing protection", () => {
    expect(PaperOrderSchema.safeParse({ ...base, stopLoss: "95", takeProfit: "120" }).success).toBe(true);
    expect(PaperOrderSchema.safeParse({ ...base, type: "limit", limitPrice: "99.5" }).success).toBe(true);
  });
  it("rejects floats, bad limits, protection on sells, inverted protection, unknown keys", () => {
    expect(PaperOrderSchema.safeParse({ ...base, quantity: 0.5 }).success).toBe(false);
    expect(PaperOrderSchema.safeParse({ ...base, quantity: "1e3" }).success).toBe(false);
    expect(PaperOrderSchema.safeParse({ ...base, type: "limit" }).success).toBe(false);
    expect(PaperOrderSchema.safeParse({ ...base, limitPrice: "100" }).success).toBe(false);
    expect(PaperOrderSchema.safeParse({ ...base, side: "sell", stopLoss: "90" }).success).toBe(false);
    expect(PaperOrderSchema.safeParse({ ...base, stopLoss: "120", takeProfit: "95" }).success).toBe(false);
    expect(PaperOrderSchema.safeParse({ ...base, type: "limit", limitPrice: "100", stopLoss: "101" }).success).toBe(false);
    expect(PaperOrderSchema.safeParse({ ...base, quantity: "0" }).success).toBe(false);
    expect(PaperOrderSchema.safeParse({ ...base, userId: "someone-else" }).success).toBe(false);
  });
  it("bounds the virtual starting balance", () => {
    expect(PaperAccountActionSchema.parse({ action: "open" }).startingBalance).toBe("10000");
    expect(PaperAccountActionSchema.safeParse({ action: "reset", startingBalance: "50" }).success).toBe(false);
  });
});

describe("paper account valuation", () => {
  const now = Date.parse("2026-01-01T00:01:00Z");
  const pos = { pairId: 1, symbol: "BTCUSDT", quantity: "10", costBasis: "1001.5005", realizedPnl: "-5", stopLoss: null, takeProfit: null };
  it("values positions with fresh prices exactly (decimal)", () => {
    const v = valueAccount("8998.4995", [pos], [{ pairId: 1, price: "100.1", fetchedAt: "2026-01-01T00:00:30Z" }], now);
    expect(v.equity).toBe("9999.49950000");
    expect(v.positions[0]).toMatchObject({ avgEntry: "100.15005000", marketValue: "1001.00000000", unrealizedPnl: "-0.50050000", priceStale: false });
    expect(v.realizedPnl).toBe("-5.00000000");
  });
  it("never values a position with a stale or missing price", () => {
    const v = valueAccount("100", [pos], [{ pairId: 1, price: "100", fetchedAt: "2025-12-31T23:00:00Z" }], now);
    expect(v.equity).toBeNull();
    expect(v.positions[0]).toMatchObject({ priceStale: true, marketValue: null, unrealizedPnl: null });
    expect(valueAccount("100", [{ ...pos, quantity: "0", costBasis: "0" }], [], now).equity).toBe("100.00000000");
  });
});
