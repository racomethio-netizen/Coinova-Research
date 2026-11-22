"use client";

import Link from "next/link";
import { Badge, Card, Loading, PageHeader } from "@/components/ui";
import { pct } from "@/lib/format";
import { useApi } from "@/lib/hooks";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

function Rows({ rows }: { rows: [string, string | number | boolean | null | undefined][] }) {
  return (
    <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-sm">
      {rows.map(([k, v]) => <div key={k} className="contents"><dt className="text-muted">{k}</dt><dd className="num">{v == null ? "—" : String(v)}</dd></div>)}
    </dl>
  );
}

export default function SettingsPage() {
  const { data, error, loading } = useApi<Any>("/settings/summary");
  const c = data?.config;
  return (
    <>
      <PageHeader title="Settings" subtitle="Effective configuration, read-only. No secrets are shown. Absolute risk ceilings can never be exceeded." />
      <Loading error={error} loading={loading && !data} />
      {data && (
        <div className="space-y-4">
          <p className="rounded-lg border border-line bg-elevated p-3 text-xs text-muted">{data.how_to_change}</p>
          <div className="grid gap-4 md:grid-cols-2">
            <Card title="Trading">
              <Rows rows={[["Mode", c.trading_mode], ["Pairs", c.trading_pairs.join(", ")], ["Exchanges", c.enabled_exchanges.join(", ")],
                ["Spot", c.spot_enabled ? "enabled" : "disabled"], ["Futures", c.futures_enabled ? "enabled" : "disabled"],
                ["Short selling", c.short_selling_enabled ? "enabled" : "disabled"], ["Martingale", c.martingale_enabled ? "enabled" : "never"]]} />
            </Card>
            <Card title="Risk limits">
              <Rows rows={[["Risk per trade", pct(c.risk.risk_per_trade, 2)], ["Daily loss limit", pct(c.risk.daily_loss_limit, 1)],
                ["Weekly loss limit", pct(c.risk.weekly_loss_limit, 1)], ["Max drawdown (shutdown)", pct(c.risk.max_drawdown, 0)],
                ["Max open positions", c.risk.max_open_positions], ["Max exposure", pct(c.risk.max_portfolio_exposure, 0)],
                ["Max per asset", pct(data.risk_extra.max_asset_concentration, 0)], ["Max correlated exposure", pct(data.risk_extra.max_correlated_exposure, 0)],
                ["Correlation threshold", data.risk_extra.correlation_threshold], ["Cooldown after a loss", `${data.risk_extra.cooldown_after_stop_minutes} min`],
                ["News pause above", data.news_block_threshold]]} />
            </Card>
            <Card title="Paper simulation">
              <Rows rows={[["Starting capital", data.paper_simulation.initial_capital], ["Fees", `${data.paper_simulation.fee_bps} bps`],
                ["Extra slippage", `${data.paper_simulation.slippage_bps} bps`], ["Order delay", `${data.paper_simulation.latency_ms} ms`],
                ["Book participation", pct(data.paper_simulation.book_participation, 0)], ["Stale book after", `${data.paper_simulation.max_book_age_seconds} s`],
                ["Injected outages", pct(data.paper_simulation.outage_rate, 1)]]} />
            </Card>
            <Card title="Integrations">
              <Rows rows={[["Telegram", c.telegram_configured ? "configured" : "not configured"], ["X sentiment", data.x.configured ? `on (${data.x.coins.join(", ")}, every ${data.x.refresh_minutes} min, max $${data.x.daily_budget_usd}/day)` : "not configured"]]} />
              <p className="mt-2 text-xs"><Link className="underline" href="/connection/">Binance connection</Link> · <Link className="underline" href="/telegram/">Telegram</Link> · <Link className="underline" href="/security/">Security</Link></p>
            </Card>
          </div>
          <Card title="Live trading" actions={<Badge tone={data.live_trading_blockers.length ? "good" : "bad"}>{data.live_trading_blockers.length ? "blocked" : "permitted by config"}</Badge>}>
            <p className="mb-2 text-sm">Live trading needs every gate cleared. Currently blocking:</p>
            <ul className="list-disc pl-5 text-sm text-warn">{data.live_trading_blockers.map((b: string) => <li key={b}>{b}</li>)}</ul>
          </Card>
        </div>
      )}
    </>
  );
}
