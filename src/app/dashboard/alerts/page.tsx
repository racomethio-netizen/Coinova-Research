import Link from "next/link";
import { Badge, ButtonLink, Card, CardBody, CardHeader, EmptyState, Notice, PageHeader } from "@/components/ui";
import { TIER_LIMITS } from "@/config/plans";
import { describeAlert } from "@/lib/alerts/evaluate";
import { hasTier, requireUserPage } from "@/lib/auth/session";
import { fmtDate } from "@/lib/format";
import { getMarketService } from "@/lib/market";
import { getAlertHistory, listAlerts } from "@/server/alerts";
import { listPortfolios } from "@/server/portfolio";
import { AlertForm, AlertRowActions } from "./client";

export default async function AlertsPage() {
  const viewer = await requireUserPage("/dashboard/alerts");
  const premium = hasTier(viewer, "premium");
  const [alerts, history, portfolios, markets, telegram] = await Promise.all([
    listAlerts(viewer.supabase, viewer.user.id),
    getAlertHistory(viewer.supabase, viewer.user.id, 30),
    listPortfolios(viewer.supabase, viewer.user.id),
    getMarketService().getUniverseMarkets().catch(() => null),
    viewer.supabase.from("telegram_accounts").select("user_id").eq("user_id", viewer.user.id).maybeSingle(),
  ]);
  const names = new Map((markets?.data ?? []).map((m) => [m.id, `${m.name}`]));
  const pnames = new Map(portfolios.map((p) => [p.id, p.name]));
  const limit = TIER_LIMITS[viewer.tier].alerts;

  return (
    <div className="space-y-6">
      <PageHeader title="Alerts" description={`Get notified when conditions are met. ${premium ? `Your plan allows ${limit} alerts.` : ""}`} />
      <Notice>
        Alerts are evaluated by a scheduled job (about every 5 minutes) using the latest retrieved market data, so notifications can lag real-time prices.
        Each alert fires once when its condition becomes true and re-arms after it stops being true, subject to its cooldown.
      </Notice>
      {!premium ? (
        <EmptyState title="Alerts require Premium" description="Upgrade to create price, change, score, unlock, and portfolio alerts." action={<ButtonLink href="/pricing">View plans</ButtonLink>} />
      ) : (
        <Card>
          <CardHeader title="New alert" />
          <CardBody>
            <AlertForm
              coins={(markets?.data ?? []).map((c) => ({ id: c.id, label: `${c.name} (${c.symbol})` }))}
              portfolios={portfolios.map((p) => ({ id: p.id, label: p.name }))}
              telegramLinked={Boolean(telegram.data)}
              disabled={alerts.length >= limit}
            />
            {alerts.length >= limit ? <p className="mt-2 text-sm text-warning">You have reached your plan&apos;s alert limit.</p> : null}
            {!telegram.data ? (
              <p className="mt-3 text-xs text-faint">
                To receive Telegram alerts, link your account in <Link href="/dashboard/settings" className="underline">Settings</Link>.
              </p>
            ) : null}
          </CardBody>
        </Card>
      )}

      <Card>
        <CardHeader title="Your alerts" />
        <CardBody>
          {alerts.length === 0 ? (
            <p className="text-sm text-muted">No alerts configured.</p>
          ) : (
            <ul className="divide-y divide-line">
              {alerts.map((a) => (
                <li key={a.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                  <div className="min-w-0">
                    <p className="text-sm font-medium">{describeAlert(a.type, a.threshold, a.coinId ? names.get(a.coinId) ?? a.coinId : a.portfolioId ? pnames.get(a.portfolioId) : null)}</p>
                    <p className="mt-0.5 flex flex-wrap gap-2 text-xs text-faint">
                      {a.isActive ? <Badge tone="positive">Active</Badge> : <Badge>Paused</Badge>}
                      <span>{a.channels.join(", ")}</span>
                      <span>{a.triggerMode === "once" ? "fires once" : `cooldown ${a.cooldownMinutes} min`}</span>
                      <span>last evaluated {fmtDate(a.lastEvaluatedAt, { time: true })}</span>
                      {a.lastTriggeredAt ? <span>last triggered {fmtDate(a.lastTriggeredAt, { time: true })}</span> : null}
                    </p>
                  </div>
                  <AlertRowActions id={a.id} isActive={a.isActive} canEnable={premium} />
                </li>
              ))}
            </ul>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Alert history" description="Recent triggers and evaluations skipped because of missing or stale data." />
        <CardBody>
          {history.length === 0 ? (
            <p className="text-sm text-muted">No alert activity yet.</p>
          ) : (
            <ul className="space-y-2 text-sm">
              {history.map((h) => (
                <li key={h.id} className="flex flex-wrap justify-between gap-2">
                  <span>
                    <Badge tone={h.outcome === "triggered" ? "accent" : "warning"}>{h.outcome}</Badge> <span className="text-muted">{h.reason}</span>
                  </span>
                  <span className="text-xs text-faint">
                    {fmtDate(h.evaluated_at, { time: true })}
                    {h.data_as_of ? ` · data as of ${fmtDate(h.data_as_of, { time: true })}` : ""}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </CardBody>
      </Card>
    </div>
  );
}
