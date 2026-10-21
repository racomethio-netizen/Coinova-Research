import { describe, expect, it } from "vitest";
import { computeHoldings, summarizePortfolio, validateLedger, PortfolioCalculationError, type PortfolioTransaction } from "@/lib/portfolio/calc";
import { escapeCsvText, transactionsToCsv } from "@/lib/portfolio/csv";
import { TransactionInputSchema } from "@/lib/portfolio/schemas";

let seq = 0;
const tx = (p: Partial<PortfolioTransaction> & Pick<PortfolioTransaction, "type" | "quantity">): PortfolioTransaction => ({
  id: `t${++seq}`,
  coinId: "btc",
  priceUsd: null,
  feeUsd: "0",
  occurredAt: `2026-01-${String(10 + seq).padStart(2, "0")}T00:00:00Z`,
  ...p,
});

describe("computeHoldings (average cost)", () => {
  it("accumulates buys with fees in cost basis", () => {
    const h = computeHoldings([tx({ type: "buy", quantity: "1", priceUsd: "100", feeUsd: "1" }), tx({ type: "buy", quantity: "1", priceUsd: "200", feeUsd: "1" })]).get("btc")!;
    expect(h.quantity.toString()).toBe("2");
    expect(h.costBasis.toString()).toBe("302");
    expect(h.averageCost!.toString()).toBe("151");
  });

  it("realizes P&L on sale net of fees using average cost", () => {
    const h = computeHoldings([
      tx({ type: "buy", quantity: "2", priceUsd: "100" }),
      tx({ type: "buy", quantity: "2", priceUsd: "200" }),
      tx({ type: "sell", quantity: "1", priceUsd: "300", feeUsd: "2" }),
    ]).get("btc")!;
    // avg = 150; proceeds = 300 - 2 = 298; realized = 148
    expect(h.realizedPnl.toString()).toBe("148");
    expect(h.quantity.toString()).toBe("3");
    expect(h.costBasis.toString()).toBe("450");
  });

  it("avoids floating-point error (0.1 + 0.2)", () => {
    const h = computeHoldings([tx({ type: "buy", quantity: "0.1", priceUsd: "1" }), tx({ type: "buy", quantity: "0.2", priceUsd: "1" })]).get("btc")!;
    expect(h.quantity.toString()).toBe("0.3");
    expect(h.costBasis.toString()).toBe("0.3");
  });

  it("transfer_out removes basis without realizing gains, fee is a realized loss", () => {
    const h = computeHoldings([tx({ type: "buy", quantity: "4", priceUsd: "10" }), tx({ type: "transfer_out", quantity: "1", feeUsd: "0.5" })]).get("btc")!;
    expect(h.quantity.toString()).toBe("3");
    expect(h.costBasis.toString()).toBe("30");
    expect(h.realizedPnl.toString()).toBe("-0.5");
  });

  it("transfer_in with zero cost basis (airdrop) is allowed", () => {
    const h = computeHoldings([tx({ type: "transfer_in", quantity: "5", priceUsd: "0" })]).get("btc")!;
    expect(h.quantity.toString()).toBe("5");
    expect(h.costBasis.toString()).toBe("0");
  });

  it("rejects selling more than held at that point in time", () => {
    expect(() => computeHoldings([tx({ type: "buy", quantity: "1", priceUsd: "1" }), tx({ type: "sell", quantity: "1.5", priceUsd: "1" })])).toThrow(PortfolioCalculationError);
  });

  it("orders by occurredAt, so a back-dated sale before the buy fails", () => {
    const err = validateLedger([
      { id: "a", coinId: "btc", type: "buy", quantity: "1", priceUsd: "1", feeUsd: "0", occurredAt: "2026-02-01T00:00:00Z" },
      { id: "b", coinId: "btc", type: "sell", quantity: "1", priceUsd: "1", feeUsd: "0", occurredAt: "2026-01-01T00:00:00Z" },
    ]);
    expect(err).toMatch(/Insufficient quantity/);
  });

  it("rejects invalid quantities, prices and fees", () => {
    expect(() => computeHoldings([tx({ type: "buy", quantity: "0", priceUsd: "1" })])).toThrow(/positive/);
    expect(() => computeHoldings([tx({ type: "buy", quantity: "abc", priceUsd: "1" })])).toThrow(/Invalid quantity/);
    expect(() => computeHoldings([tx({ type: "buy", quantity: "1", priceUsd: "-1" })])).toThrow(/negative/);
    expect(() => computeHoldings([tx({ type: "buy", quantity: "1", priceUsd: "1", feeUsd: "-1" })])).toThrow(/negative/);
    expect(() => computeHoldings([tx({ type: "buy", quantity: "1" })])).toThrow(/Missing price/);
  });

  it("resets cost basis when position fully closed", () => {
    const h = computeHoldings([tx({ type: "buy", quantity: "3", priceUsd: "10" }), tx({ type: "sell", quantity: "3", priceUsd: "20" })]).get("btc")!;
    expect(h.quantity.isZero()).toBe(true);
    expect(h.costBasis.isZero()).toBe(true);
    expect(h.averageCost).toBeNull();
    expect(h.realizedPnl.toString()).toBe("30");
  });
});

describe("summarizePortfolio", () => {
  it("computes market value, unrealized P&L and allocation", () => {
    const s = summarizePortfolio(
      [
        { id: "1", coinId: "btc", type: "buy", quantity: "1", priceUsd: "100", feeUsd: "0", occurredAt: "2026-01-01T00:00:00Z" },
        { id: "2", coinId: "eth", type: "buy", quantity: "10", priceUsd: "10", feeUsd: "0", occurredAt: "2026-01-01T00:00:00Z" },
      ],
      new Map([["btc", 150], ["eth", 5]]),
    );
    expect(s.totalMarketValue.toString()).toBe("200");
    expect(s.totalCostBasis.toString()).toBe("200");
    expect(s.totalUnrealizedPnl.toString()).toBe("0");
    const btc = s.holdings.find((h) => h.coinId === "btc")!;
    expect(btc.unrealizedPnl!.toString()).toBe("50");
    expect(btc.unrealizedPnlPct!.toString()).toBe("50");
    expect(btc.allocationPct!.toString()).toBe("75");
  });

  it("excludes unpriced holdings from totals and reports them", () => {
    const s = summarizePortfolio([{ id: "1", coinId: "x", type: "buy", quantity: "1", priceUsd: "100", feeUsd: "0", occurredAt: "2026-01-01T00:00:00Z" }], new Map([["x", null]]));
    expect(s.unpricedCoinIds).toEqual(["x"]);
    expect(s.totalMarketValue.toString()).toBe("0");
    expect(s.holdings[0]!.marketValue).toBeNull();
  });
});

describe("CSV export", () => {
  it("escapes quotes, commas and newlines", () => {
    expect(escapeCsvText('a,"b"\nc')).toBe('"a,""b""\nc"');
  });
  it("neutralizes spreadsheet formula injection in text fields", () => {
    expect(escapeCsvText("=HYPERLINK(\"x\")")).toBe("\"'=HYPERLINK(\"\"x\"\")\"");
    expect(escapeCsvText("+1")).toBe("'+1");
    expect(escapeCsvText("@SUM")).toBe("'@SUM");
  });
  it("produces a header and one row per transaction", () => {
    const csv = transactionsToCsv([
      { id: "1", coinId: "btc", type: "buy", quantity: "1.5", priceUsd: "100", feeUsd: "0", occurredAt: "2026-01-01T00:00:00Z", portfolioName: "Main", coinSymbol: "BTC", notes: "=cmd" },
    ]);
    const lines = csv.trim().split("\r\n");
    expect(lines).toHaveLength(2);
    expect(lines[0]).toMatch(/^occurred_at,portfolio/);
    expect(lines[1]).toContain("1.5,100,0,'=cmd,1");
  });
});

describe("TransactionInputSchema", () => {
  const base = { portfolioId: "8f8a3f8a-1b2c-4d5e-8f90-123456789abc", coinId: "bitcoin", type: "buy", quantity: "1", priceUsd: "100", occurredAt: "2026-01-01T00:00:00Z" };
  it("accepts valid input", () => {
    expect(TransactionInputSchema.safeParse(base).success).toBe(true);
  });
  it("rejects future dates, too many decimals, negative and zero quantities", () => {
    expect(TransactionInputSchema.safeParse({ ...base, occurredAt: "2999-01-01T00:00:00Z" }).success).toBe(false);
    expect(TransactionInputSchema.safeParse({ ...base, quantity: "1.0000000000000000001" }).success).toBe(false);
    expect(TransactionInputSchema.safeParse({ ...base, quantity: "-1" }).success).toBe(false);
    expect(TransactionInputSchema.safeParse({ ...base, quantity: "0" }).success).toBe(false);
  });
  it("requires a price for buys and transfers in", () => {
    expect(TransactionInputSchema.safeParse({ ...base, priceUsd: undefined }).success).toBe(false);
    expect(TransactionInputSchema.safeParse({ ...base, type: "transfer_in", priceUsd: undefined }).success).toBe(false);
    expect(TransactionInputSchema.safeParse({ ...base, type: "transfer_out", priceUsd: undefined }).success).toBe(true);
  });
  it("rejects unknown types and malformed coin IDs", () => {
    expect(TransactionInputSchema.safeParse({ ...base, type: "stake" }).success).toBe(false);
    expect(TransactionInputSchema.safeParse({ ...base, coinId: "Bitcoin;drop" }).success).toBe(false);
  });
});
