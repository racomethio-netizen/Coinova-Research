import { authed } from "@/lib/http/authed";
import { ok, parseJsonBody } from "@/lib/http/api";
import { PortfolioInputSchema } from "@/lib/portfolio/schemas";
import { createPortfolio, getPortfolioOverview } from "@/server/portfolio";

export const dynamic = "force-dynamic";

const dec = (d: { toString(): string } | null) => (d === null ? null : d.toString());

/** Portfolios with holdings valued at current cached prices. Readable by any signed-in owner. */
export const GET = authed("GET /api/portfolio", {}, async ({ viewer }) => {
  const o = await getPortfolioOverview(viewer.supabase, viewer.user.id);
  return ok({
    pricing: o.pricing,
    portfolios: o.portfolios.map(({ portfolio, summary, error }) => ({
      ...portfolio,
      error,
      summary: summary && {
        totalMarketValue: dec(summary.totalMarketValue),
        totalCostBasis: dec(summary.totalCostBasis),
        totalUnrealizedPnl: dec(summary.totalUnrealizedPnl),
        totalRealizedPnl: dec(summary.totalRealizedPnl),
        unpricedCoinIds: summary.unpricedCoinIds,
        holdings: summary.holdings.map((h) => ({
          coinId: h.coinId,
          quantity: dec(h.quantity),
          costBasis: dec(h.costBasis),
          averageCost: dec(h.averageCost),
          realizedPnl: dec(h.realizedPnl),
          priceUsd: dec(h.priceUsd),
          marketValue: dec(h.marketValue),
          unrealizedPnl: dec(h.unrealizedPnl),
          unrealizedPnlPct: dec(h.unrealizedPnlPct),
          allocationPct: dec(h.allocationPct),
        })),
      },
    })),
    transactions: o.transactions,
  });
});

/** Create a portfolio (Premium; also enforced by a database trigger). */
export const POST = authed("POST /api/portfolio", { tier: "premium", perMinute: 20 }, async ({ req, viewer }) => {
  const body = await parseJsonBody(req, PortfolioInputSchema);
  return ok(await createPortfolio(viewer.supabase, viewer.user.id, body.name), { status: 201 });
});
