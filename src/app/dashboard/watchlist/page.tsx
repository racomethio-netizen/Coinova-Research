import Link from "next/link";
import { CoinIdentity, FreshnessBadge, PriceChange } from "@/components/market/bits";
import { Card, CardBody, CardHeader, EmptyState, PageHeader } from "@/components/ui";
import { TIER_LIMITS } from "@/config/plans";
import { requireUserPage } from "@/lib/auth/session";
import { fmtUsd } from "@/lib/format";
import { getMarketService } from "@/lib/market";
import { listWatchlists } from "@/server/watchlists";
import { AddCoinForm, NewWatchlistForm, RemoveCoinButton, WatchlistActions } from "./client";

export default async function WatchlistPage() {
  const viewer = await requireUserPage("/dashboard/watchlist");
  const [lists, markets] = await Promise.all([listWatchlists(viewer.supabase, viewer.user.id), getMarketService().getUniverseMarkets().catch(() => null)]);
  const byId = new Map((markets?.data ?? []).map((m) => [m.id, m]));
  const coins = (markets?.data ?? []).map((c) => ({ id: c.id, label: `${c.name} (${c.symbol})` }));
  const limits = TIER_LIMITS[viewer.tier];

  return (
    <div className="space-y-6">
      <PageHeader title="Watchlists" description={`Your plan allows ${limits.watchlists} watchlist${limits.watchlists === 1 ? "" : "s"} with up to ${limits.watchlistItems} coins each.`} />
      {markets ? <FreshnessBadge source={markets.source} fetchedAt={markets.fetchedAt} stale={markets.stale} /> : null}
      {lists.length === 0 ? <EmptyState title="No watchlists yet" description="Create a watchlist to follow coins you care about." action={<NewWatchlistForm />} /> : null}
      {lists.map((w) => (
        <Card key={w.id}>
          <CardHeader title={w.name} description={`${w.coinIds.length} / ${limits.watchlistItems} coins`} action={<WatchlistActions id={w.id} name={w.name} />} />
          <CardBody className="space-y-4">
            {w.coinIds.length === 0 ? (
              <p className="text-sm text-muted">This watchlist is empty.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[520px] text-sm">
                  <thead className="text-left text-xs uppercase text-faint">
                    <tr>
                      <th className="py-2">Asset</th>
                      <th className="py-2 text-right">Price</th>
                      <th className="py-2 text-right">24h</th>
                      <th className="py-2 text-right">7d</th>
                      <th className="py-2 text-right">Market cap</th>
                      <th className="py-2"><span className="sr-only">Remove</span></th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line">
                    {w.coinIds.map((id) => {
                      const m = byId.get(id);
                      return (
                        <tr key={id}>
                          <td className="py-2">
                            <Link href={`/coins/${id}`} className="hover:text-accent">
                              <CoinIdentity name={m?.name ?? id} symbol={m?.symbol ?? ""} imageUrl={m?.imageUrl ?? null} size={20} />
                            </Link>
                          </td>
                          <td className="tabular py-2 text-right">{fmtUsd(m?.priceUsd)}</td>
                          <td className="py-2 text-right"><PriceChange value={m?.change24hPct} /></td>
                          <td className="py-2 text-right"><PriceChange value={m?.change7dPct} /></td>
                          <td className="tabular py-2 text-right">{fmtUsd(m?.marketCapUsd, { compact: true })}</td>
                          <td className="py-2 text-right"><RemoveCoinButton watchlistId={w.id} coinId={id} /></td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
            <AddCoinForm watchlistId={w.id} coins={coins.filter((c) => !w.coinIds.includes(c.id))} />
          </CardBody>
        </Card>
      ))}
      {lists.length > 0 && lists.length < limits.watchlists ? (
        <Card>
          <CardHeader title="New watchlist" />
          <CardBody>
            <NewWatchlistForm />
          </CardBody>
        </Card>
      ) : null}
    </div>
  );
}
