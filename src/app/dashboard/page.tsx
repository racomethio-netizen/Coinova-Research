import Link from "next/link";
import { PriceChange } from "@/components/market/bits";
import { ButtonLink, Card, CardBody, CardHeader, Notice, PageHeader, Stat } from "@/components/ui";
import { TIER_LIMITS } from "@/config/plans";
import { requireUserPage, hasTier } from "@/lib/auth/session";
import { fmtDate, fmtUsd } from "@/lib/format";
import { getMarketService } from "@/lib/market";
import { listAlerts } from "@/server/alerts";
import { listNotifications } from "@/server/notifications";
import { getPortfolioOverview } from "@/server/portfolio";
import { listWatchlists } from "@/server/watchlists";

export default async function DashboardPage({ searchParams }: { searchParams: Promise<{ notice?: string; error?: string }> }) {
  const sp = await searchParams;
  const viewer = await requireUserPage();
  const sb = viewer.supabase;
  const uid = viewer.user.id;
  const [overview, watchlists, alerts, notifications, markets] = await Promise.all([
    getPortfolioOverview(sb, uid).catch(() => null),
    listWatchlists(sb, uid).catch(() => []),
    listAlerts(sb, uid).catch(() => []),
    listNotifications(sb, uid, 5).catch(() => []),
    getMarketService().getUniverseMarkets().catch(() => null),
  ]);
  const totalValue = overview?.portfolios.reduce((s, p) => s + (p.summary ? Number(p.summary.totalMarketValue.toString()) : 0), 0) ?? 0;
  const watched = [...new Set(watchlists.flatMap((w) => w.coinIds))].slice(0, 8);
  const marketById = new Map((markets?.data ?? []).map((m) => [m.id, m]));
  const premium = hasTier(viewer, "premium");

  return (
    <div className="space-y-6">
      <PageHeader title="Overview" description="Your portfolios, watchlists, and recent alerts." />
      {sp.notice === "password-updated" ? <Notice tone="positive">Your password has been updated.</Notice> : null}
      {sp.error === "forbidden" ? <Notice tone="negative">You do not have access to that area.</Notice> : null}
      {!premium ? (
        <Notice tone="accent" title="You are on the Free plan">
          Portfolios, alerts, full research scores, and premium articles are available with Premium.{" "}
          <Link href="/pricing" className="text-accent underline">
            Compare plans
          </Link>
        </Notice>
      ) : null}

      <div className="grid gap-4 sm:grid-cols-3">
        <Card>
          <CardBody>
            <dl>
              <Stat label="Portfolio value (est.)" value={premium || totalValue > 0 ? fmtUsd(totalValue) : "—"} sub={overview?.pricing ? `Prices retrieved ${fmtDate(overview.pricing.fetchedAt, { time: true })}` : undefined} />
            </dl>
          </CardBody>
        </Card>
        <Card>
          <CardBody>
            <dl>
              <Stat label="Watchlists" value={`${watchlists.length} / ${TIER_LIMITS[viewer.tier].watchlists}`} />
            </dl>
          </CardBody>
        </Card>
        <Card>
          <CardBody>
            <dl>
              <Stat label="Active alerts" value={`${alerts.filter((a) => a.isActive).length} / ${TIER_LIMITS[viewer.tier].alerts}`} />
            </dl>
          </CardBody>
        </Card>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader title="Watched coins" action={<Link className="text-sm text-accent" href="/dashboard/watchlist">Manage</Link>} />
          <CardBody>
            {watched.length === 0 ? (
              <p className="text-sm text-muted">
                You are not watching any coins yet. Browse <Link className="text-accent underline" href="/markets">markets</Link> and add some.
              </p>
            ) : (
              <ul className="divide-y divide-line text-sm">
                {watched.map((id) => {
                  const m = marketById.get(id);
                  return (
                    <li key={id} className="flex items-center justify-between py-2">
                      <Link href={`/coins/${id}`} className="hover:text-accent">
                        {m?.name ?? id}
                      </Link>
                      <span className="flex gap-3">
                        <span className="tabular">{fmtUsd(m?.priceUsd)}</span>
                        <PriceChange value={m?.change24hPct} />
                      </span>
                    </li>
                  );
                })}
              </ul>
            )}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Recent notifications" action={<Link className="text-sm text-accent" href="/dashboard/notifications">View all</Link>} />
          <CardBody>
            {notifications.length === 0 ? (
              <p className="text-sm text-muted">No notifications yet.</p>
            ) : (
              <ul className="space-y-3 text-sm">
                {notifications.map((n) => (
                  <li key={n.id}>
                    <p className={n.readAt ? "text-muted" : "font-medium"}>{n.title}</p>
                    <p className="text-xs text-faint">{fmtDate(n.createdAt, { time: true })}</p>
                  </li>
                ))}
              </ul>
            )}
          </CardBody>
        </Card>
      </div>
      {!premium ? <ButtonLink href="/pricing">Upgrade to Premium</ButtonLink> : null}
    </div>
  );
}
