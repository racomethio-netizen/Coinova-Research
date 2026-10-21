import Decimal from "decimal.js";

/**
 * Portfolio accounting with decimal arithmetic.
 *
 * METHODOLOGY (see docs/ARCHITECTURE.md#portfolio-methodology):
 * - Quote currency: USD. All prices and fees are USD amounts.
 * - Cost basis: weighted AVERAGE COST per asset.
 * - buy:          qty += q; costBasis += q × price + fee
 * - sell:         requires q ≤ qty. proceeds = q × price − fee;
 *                 costRemoved = avgCost × q; realizedPnl += proceeds − costRemoved
 * - transfer_in:  qty += q; costBasis += q × price + fee, where price is the
 *                 user-declared cost basis per unit (0 is allowed, e.g. airdrops).
 * - transfer_out: requires q ≤ qty. Removes avgCost × q of basis with no
 *                 realized gain; any fee is recorded as a realized loss.
 * - Transactions are applied in (occurredAt, createdAt, id) order.
 * - Unrealized P&L = q × currentPrice − costBasis (only when a price is known).
 *
 * Transactions are user-entered records; they are NOT verified on-chain.
 */

Decimal.set({ precision: 40, rounding: Decimal.ROUND_HALF_EVEN });

export type TransactionType = "buy" | "sell" | "transfer_in" | "transfer_out";

export interface PortfolioTransaction {
  id: string;
  coinId: string;
  type: TransactionType;
  /** Decimal strings, as stored in Postgres NUMERIC columns. */
  quantity: string;
  priceUsd: string | null;
  feeUsd: string;
  occurredAt: string;
  createdAt?: string;
}

export interface Holding {
  coinId: string;
  quantity: Decimal;
  costBasis: Decimal;
  averageCost: Decimal | null;
  realizedPnl: Decimal;
  totalFees: Decimal;
}

export interface ValuedHolding extends Holding {
  priceUsd: Decimal | null;
  marketValue: Decimal | null;
  unrealizedPnl: Decimal | null;
  unrealizedPnlPct: Decimal | null;
  allocationPct: Decimal | null;
}

export interface PortfolioSummary {
  holdings: ValuedHolding[];
  totalMarketValue: Decimal;
  totalCostBasis: Decimal;
  totalUnrealizedPnl: Decimal;
  totalRealizedPnl: Decimal;
  /** Holdings with quantity > 0 that have no current price (excluded from totals). */
  unpricedCoinIds: string[];
}

export class PortfolioCalculationError extends Error {
  constructor(
    message: string,
    public readonly transactionId: string,
  ) {
    super(message);
    this.name = "PortfolioCalculationError";
  }
}

function dec(v: string | null | undefined, field: string, txId: string): Decimal {
  if (v === null || v === undefined || v === "") throw new PortfolioCalculationError(`Missing ${field}.`, txId);
  let d: Decimal;
  try {
    d = new Decimal(v);
  } catch {
    throw new PortfolioCalculationError(`Invalid ${field}.`, txId);
  }
  if (!d.isFinite()) throw new PortfolioCalculationError(`Invalid ${field}.`, txId);
  return d;
}

export function sortTransactions<T extends PortfolioTransaction>(txs: readonly T[]): T[] {
  return [...txs].sort((a, b) => {
    const t = Date.parse(a.occurredAt) - Date.parse(b.occurredAt);
    if (t !== 0) return t;
    const c = Date.parse(a.createdAt ?? a.occurredAt) - Date.parse(b.createdAt ?? b.occurredAt);
    if (c !== 0) return c;
    return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
  });
}

export function computeHoldings(transactions: readonly PortfolioTransaction[]): Map<string, Holding> {
  const holdings = new Map<string, Holding>();
  for (const tx of sortTransactions(transactions)) {
    const q = dec(tx.quantity, "quantity", tx.id);
    if (q.lte(0)) throw new PortfolioCalculationError("Quantity must be positive.", tx.id);
    const fee = dec(tx.feeUsd ?? "0", "fee", tx.id);
    if (fee.isNeg()) throw new PortfolioCalculationError("Fee cannot be negative.", tx.id);

    const h =
      holdings.get(tx.coinId) ??
      ({ coinId: tx.coinId, quantity: new Decimal(0), costBasis: new Decimal(0), averageCost: null, realizedPnl: new Decimal(0), totalFees: new Decimal(0) } as Holding);
    h.totalFees = h.totalFees.plus(fee);

    switch (tx.type) {
      case "buy":
      case "transfer_in": {
        const price = dec(tx.priceUsd, "price", tx.id);
        if (price.isNeg()) throw new PortfolioCalculationError("Price cannot be negative.", tx.id);
        h.quantity = h.quantity.plus(q);
        h.costBasis = h.costBasis.plus(q.times(price)).plus(fee);
        break;
      }
      case "sell":
      case "transfer_out": {
        if (q.gt(h.quantity)) {
          throw new PortfolioCalculationError(
            `Insufficient quantity: tried to ${tx.type === "sell" ? "sell" : "transfer out"} ${q.toString()} but only ${h.quantity.toString()} held at that time.`,
            tx.id,
          );
        }
        const avg = h.quantity.isZero() ? new Decimal(0) : h.costBasis.div(h.quantity);
        const costRemoved = avg.times(q);
        if (tx.type === "sell") {
          const price = dec(tx.priceUsd, "price", tx.id);
          if (price.isNeg()) throw new PortfolioCalculationError("Price cannot be negative.", tx.id);
          h.realizedPnl = h.realizedPnl.plus(q.times(price).minus(fee).minus(costRemoved));
        } else {
          h.realizedPnl = h.realizedPnl.minus(fee);
        }
        h.quantity = h.quantity.minus(q);
        h.costBasis = h.quantity.isZero() ? new Decimal(0) : h.costBasis.minus(costRemoved);
        break;
      }
      default: {
        const never: never = tx.type;
        throw new PortfolioCalculationError(`Unknown transaction type ${String(never)}.`, tx.id);
      }
    }
    h.averageCost = h.quantity.gt(0) ? h.costBasis.div(h.quantity) : null;
    holdings.set(tx.coinId, h);
  }
  return holdings;
}

export function summarizePortfolio(transactions: readonly PortfolioTransaction[], prices: ReadonlyMap<string, number | null>): PortfolioSummary {
  const holdings = computeHoldings(transactions);
  const valued: ValuedHolding[] = [];
  let totalMarketValue = new Decimal(0);
  let totalCostBasis = new Decimal(0);
  let totalRealized = new Decimal(0);
  const unpriced: string[] = [];

  for (const h of holdings.values()) {
    totalRealized = totalRealized.plus(h.realizedPnl);
    const p = prices.get(h.coinId);
    const price = typeof p === "number" && Number.isFinite(p) && p >= 0 ? new Decimal(String(p)) : null;
    const marketValue = price ? h.quantity.times(price) : null;
    const unrealized = marketValue ? marketValue.minus(h.costBasis) : null;
    if (h.quantity.gt(0)) {
      if (marketValue) {
        totalMarketValue = totalMarketValue.plus(marketValue);
        totalCostBasis = totalCostBasis.plus(h.costBasis);
      } else unpriced.push(h.coinId);
    }
    valued.push({
      ...h,
      priceUsd: price,
      marketValue,
      unrealizedPnl: unrealized,
      unrealizedPnlPct: unrealized && h.costBasis.gt(0) ? unrealized.div(h.costBasis).times(100) : null,
      allocationPct: null,
    });
  }
  for (const v of valued) {
    v.allocationPct = v.marketValue && totalMarketValue.gt(0) && v.quantity.gt(0) ? v.marketValue.div(totalMarketValue).times(100) : null;
  }
  valued.sort((a, b) => (b.marketValue?.toNumber() ?? -1) - (a.marketValue?.toNumber() ?? -1));
  return {
    holdings: valued,
    totalMarketValue,
    totalCostBasis,
    totalUnrealizedPnl: totalMarketValue.minus(totalCostBasis),
    totalRealizedPnl: totalRealized,
    unpricedCoinIds: unpriced,
  };
}

/**
 * Validate that adding/editing/removing a transaction keeps the ledger valid
 * (no negative balances at any point). Returns the error message or null.
 */
export function validateLedger(transactions: readonly PortfolioTransaction[]): string | null {
  try {
    computeHoldings(transactions);
    return null;
  } catch (err) {
    if (err instanceof PortfolioCalculationError) return err.message;
    throw err;
  }
}
