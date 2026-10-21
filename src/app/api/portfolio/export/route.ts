import { authed } from "@/lib/http/authed";
import { getMarketService } from "@/lib/market";
import { transactionsToCsv } from "@/lib/portfolio/csv";
import { listPortfolios, listTransactions } from "@/server/portfolio";

export const dynamic = "force-dynamic";

/** CSV export of the user's own transactions (Premium). */
export const GET = authed("GET /api/portfolio/export", { tier: "premium", perMinute: 10 }, async ({ viewer }) => {
  const [portfolios, txs] = await Promise.all([listPortfolios(viewer.supabase, viewer.user.id), listTransactions(viewer.supabase, viewer.user.id)]);
  const names = new Map(portfolios.map((p) => [p.id, p.name]));
  const markets = await getMarketService().getUniverseMarkets().catch(() => null);
  const symbols = new Map((markets?.data ?? []).map((m) => [m.id, m.symbol]));
  const csv = transactionsToCsv(
    [...txs].reverse().map((t) => ({ ...t, portfolioName: names.get(t.portfolioId) ?? "", coinSymbol: symbols.get(t.coinId) ?? null })),
  );
  const date = new Date().toISOString().slice(0, 10);
  return new Response(csv, {
    headers: {
      "Content-Type": "text/csv; charset=utf-8",
      "Content-Disposition": `attachment; filename="transactions-${date}.csv"`,
      "Cache-Control": "private, no-store",
    },
  });
});
