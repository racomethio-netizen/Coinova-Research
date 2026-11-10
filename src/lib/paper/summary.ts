import Decimal from "decimal.js";

/** Pure valuation of a paper account (decimal.js). Prices older than MAX_PRICE_AGE_MS are not used. */
export const MAX_PRICE_AGE_MS = 60_000;

export interface PositionRow {
  pairId: number;
  symbol: string;
  quantity: string;
  costBasis: string;
  realizedPnl: string;
  stopLoss: string | null;
  takeProfit: string | null;
}

export interface PriceRow {
  pairId: number;
  price: string;
  fetchedAt: string;
}

export interface ValuedPosition extends PositionRow {
  avgEntry: string | null;
  price: string | null;
  priceStale: boolean;
  marketValue: string | null;
  unrealizedPnl: string | null;
}

export function valueAccount(cash: string, positions: PositionRow[], prices: PriceRow[], now = Date.now()) {
  const byPair = new Map(prices.map((p) => [p.pairId, p]));
  let equity: Decimal | null = new Decimal(cash);
  let realized = new Decimal(0);
  const valued: ValuedPosition[] = positions.map((p) => {
    const qty = new Decimal(p.quantity);
    realized = realized.plus(p.realizedPnl);
    const px = byPair.get(p.pairId);
    const fresh = px !== undefined && now - Date.parse(px.fetchedAt) <= MAX_PRICE_AGE_MS;
    const mv = fresh && qty.gt(0) ? qty.times(px!.price) : qty.isZero() ? new Decimal(0) : null;
    if (mv === null) equity = null;
    else if (equity !== null) equity = equity.plus(mv);
    return {
      ...p,
      avgEntry: qty.gt(0) ? new Decimal(p.costBasis).div(qty).toFixed(8) : null,
      price: px?.price ?? null,
      priceStale: !fresh,
      marketValue: mv ? mv.toFixed(8) : null,
      unrealizedPnl: mv && qty.gt(0) ? mv.minus(p.costBasis).toFixed(8) : qty.isZero() ? "0.00000000" : null,
    };
  });
  return {
    cash: new Decimal(cash).toFixed(8),
    equity: equity === null ? null : (equity as Decimal).toFixed(8),
    realizedPnl: realized.toFixed(8),
    positions: valued,
  };
}
