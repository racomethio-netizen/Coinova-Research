import Link from "next/link";
import { CoinIdentity, FreshnessBadge } from "@/components/market/bits";
import { ButtonLink, Card, CardBody, CardHeader, EmptyState, Notice, PageHeader, Stat, cn } from "@/components/ui";
import { TIER_LIMITS } from "@/config/plans";
import { hasTier, requireUserPage } from "@/lib/auth/session";
import { fmtDate, fmtDecimal, fmtPct, fmtUsd } from "@/lib/format";
import { getMarketService } from "@/lib/market";
import { getPortfolioOverview } from "@/server/portfolio";
import { NewPortfolioForm, PortfolioActions, TransactionForm, TransactionRowActions } from "./client";

const n = (d: { toString(): string } | null | undefined) => (d === null || d === undefined ? null : Number(d.toString()));

function Pnl({ value, pct }: { value: number | null; pct?: number | null }) {
  if (value === null) return <span className="text-faint">—</span>;
  return (
    <span className={cn("tabular", value > 0 ? "text-positive" : value < 0 ? "text-negative" : "")}>
      {fmtUsd(value)}
      {pct !== undefined && pct !== null ? <span className="ml-1 text-xs">({fmtPct(pct, { signed: true })})</span> : null}
    </span>
  );
}

export default async function PortfolioPage() {
  const viewer = await requireUserPage("/dashboard/portfolio");
  const premium = hasTier(viewer, "premium");
  const overview = await getPortfolioOverview(viewer.supabase, viewer.user.id);
  const svc = getMarketService();
  const coinOptions = (await svc.getUniverseMarkets().catch(() => null))?.data.map((c) => ({ id: c.id, label: `${c.name} (${c.symbol})` })) ?? [];
  const portfolios = overview.portfolios;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Portfolio"
        description="Track holdings you record manually. Values use the latest cached market prices. Transactions are not verified on-chain."
        actions={
          premium && portfolios.length > 0 ? (
            <a href="/api/portfolio/export" download className="inline-flex h-10 items-center rounded-md border border-line-strong px-4 text-sm hover:bg-subtle">
              Export CSV
            </a>
          ) : null
        }
      />
      {overview.pricing ? <FreshnessBadge source={overview.pricing.source} fetchedAt={overview.pricing.fetchedAt} stale={overview.pricing.stale} /> : <Notice tone="warning">Current prices are unavailable; market values and unrealized P&amp;L cannot be shown.</Notice>}

      {!premium ? (
        <Notice tone="accent" title="Portfolios are a Premium feature">
          {portfolios.length > 0 ? "Your existing portfolios remain visible, but adding transactions requires an active Premium membership. " : null}
          <Link href="/pricing" className="text-accent underline">
            View plans
          </Link>
        </Notice>
      ) : null}

      {portfolios.length === 0 ? (
        premium ? (
          <EmptyState title="No portfolios yet" description="Create a portfolio, then record purchases, sales, and transfers." action={<NewPortfolioForm />} />
        ) : (
          <EmptyState title="No portfolios" description="Upgrade to Premium to track portfolios with profit and loss." action={<ButtonLink href="/pricing">Upgrade</ButtonLink>} />
        )
      ) : null}

      {portfolios.map(({ portfolio, summary, error }) => {
        const txs = overview.transactions.filter((t) => t.portfolioId === portfolio.id);
        return (
          <Card key={portfolio.id}>
            <CardHeader title={portfolio.name} description={`${txs.length} transaction${txs.length === 1 ? "" : "s"}`} action={<PortfolioActions id={portfolio.id} name={portfolio.name} />} />
            <CardBody className="space-y-6">
              {error ? <Notice tone="negative" title="This portfolio's ledger is inconsistent">{error}</Notice> : null}
              {summary ? (
                <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                  <Stat label="Market value" value={fmtUsd(n(summary.totalMarketValue))} />
                  <Stat label="Cost basis" value={fmtUsd(n(summary.totalCostBasis))} />
                  <Stat label="Unrealized P&L" value={<Pnl value={n(summary.totalUnrealizedPnl)} />} />
                  <Stat label="Realized P&L" value={<Pnl value={n(summary.totalRealizedPnl)} />} />
                </dl>
              ) : null}
              {summary && summary.unpricedCoinIds.length ? (
                <Notice tone="warning">No current price for: {summary.unpricedCoinIds.join(", ")}. These holdings are excluded from totals.</Notice>
              ) : null}

              {summary && summary.holdings.some((h) => h.quantity.gt(0)) ? (
                <div className="grid gap-6 xl:grid-cols-3">
                  <div className="overflow-x-auto xl:col-span-2">
                    <table className="w-full min-w-[640px] text-sm">
                      <caption className="sr-only">Holdings</caption>
                      <thead className="text-left text-xs uppercase text-faint">
                        <tr>
                          <th className="py-2">Asset</th>
                          <th className="py-2 text-right">Quantity</th>
                          <th className="py-2 text-right">Avg cost</th>
                          <th className="py-2 text-right">Price</th>
                          <th className="py-2 text-right">Value</th>
                          <th className="py-2 text-right">Unrealized</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-line">
                        {summary.holdings
                          .filter((h) => h.quantity.gt(0))
                          .map((h) => {
                            const meta = overview.coinMeta.get(h.coinId);
                            return (
                              <tr key={h.coinId}>
                                <td className="py-2">
                                  <Link href={`/coins/${h.coinId}`} className="hover:text-accent">
                                    <CoinIdentity name={meta?.name ?? h.coinId} symbol={meta?.symbol ?? ""} imageUrl={meta?.imageUrl ?? null} size={20} />
                                  </Link>
                                </td>
                                <td className="tabular py-2 text-right">{fmtDecimal(h.quantity.toString())}</td>
                                <td className="tabular py-2 text-right">{fmtUsd(n(h.averageCost))}</td>
                                <td className="tabular py-2 text-right">{fmtUsd(n(h.priceUsd))}</td>
                                <td className="tabular py-2 text-right">{fmtUsd(n(h.marketValue))}</td>
                                <td className="py-2 text-right">
                                  <Pnl value={n(h.unrealizedPnl)} pct={n(h.unrealizedPnlPct)} />
                                </td>
                              </tr>
                            );
                          })}
                      </tbody>
                    </table>
                  </div>
                  <div>
                    <h3 className="mb-3 text-sm font-medium">Allocation</h3>
                    <ul className="space-y-2" aria-label="Allocation by market value">
                      {summary.holdings
                        .filter((h) => h.allocationPct !== null)
                        .map((h) => {
                          const pct = n(h.allocationPct)!;
                          return (
                            <li key={h.coinId} className="text-xs">
                              <div className="mb-1 flex justify-between">
                                <span>{overview.coinMeta.get(h.coinId)?.symbol ?? h.coinId}</span>
                                <span className="tabular">{fmtPct(pct, { digits: 1 })}</span>
                              </div>
                              <div className="h-2 overflow-hidden rounded-full bg-subtle" aria-hidden>
                                <div className="h-full rounded-full bg-accent" style={{ width: `${Math.max(1, pct)}%` }} />
                              </div>
                            </li>
                          );
                        })}
                    </ul>
                  </div>
                </div>
              ) : null}

              {premium ? (
                <details className="rounded-md border border-line p-4" open={txs.length === 0}>
                  <summary className="cursor-pointer text-sm font-medium">Record a transaction</summary>
                  <div className="mt-4">
                    <TransactionForm portfolioId={portfolio.id} coins={coinOptions} />
                  </div>
                </details>
              ) : null}

              <div>
                <h3 className="mb-2 text-sm font-medium">Transaction history</h3>
                {txs.length === 0 ? (
                  <p className="text-sm text-muted">No transactions recorded.</p>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full min-w-[640px] text-sm">
                      <thead className="text-left text-xs uppercase text-faint">
                        <tr>
                          <th className="py-2">Date</th>
                          <th className="py-2">Type</th>
                          <th className="py-2">Asset</th>
                          <th className="py-2 text-right">Quantity</th>
                          <th className="py-2 text-right">Price</th>
                          <th className="py-2 text-right">Fee</th>
                          <th className="py-2">
                            <span className="sr-only">Actions</span>
                          </th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-line">
                        {txs.map((t) => (
                          <tr key={t.id}>
                            <td className="py-2">{fmtDate(t.occurredAt)}</td>
                            <td className="py-2 capitalize">{t.type.replace("_", " ")}</td>
                            <td className="py-2">{overview.coinMeta.get(t.coinId)?.symbol ?? t.coinId}</td>
                            <td className="tabular py-2 text-right">{fmtDecimal(t.quantity)}</td>
                            <td className="tabular py-2 text-right">{t.priceUsd === null ? "—" : fmtUsd(Number(t.priceUsd))}</td>
                            <td className="tabular py-2 text-right">{fmtUsd(Number(t.feeUsd))}</td>
                            <td className="py-2 text-right">
                              <TransactionRowActions tx={{ id: t.id, type: t.type, quantity: t.quantity, priceUsd: t.priceUsd, feeUsd: t.feeUsd, occurredAt: t.occurredAt, notes: t.notes }} canEdit={premium} />
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            </CardBody>
          </Card>
        );
      })}

      {premium && portfolios.length > 0 && portfolios.length < TIER_LIMITS[viewer.tier].portfolios ? (
        <Card>
          <CardHeader title="Add another portfolio" />
          <CardBody>
            <NewPortfolioForm />
          </CardBody>
        </Card>
      ) : null}

      <p className="text-xs text-faint">
        P&amp;L uses the average-cost method in USD. Fees are added to cost basis on purchases and deducted from proceeds on sales; transfer-out fees are
        recorded as realized losses. Figures are estimates for your records, not tax advice.
      </p>
    </div>
  );
}
