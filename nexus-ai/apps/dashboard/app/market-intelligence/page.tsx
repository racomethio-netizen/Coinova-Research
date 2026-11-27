"use client";

import { Badge, Card, Loading, PageHeader, Table } from "@/components/ui";
import { DASH, num, pct, time } from "@/lib/format";
import { useApi } from "@/lib/hooks";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

const REGIME: Record<string, { label: string; tone: "good" | "bad" | "warn" | "muted" }> = {
  TRENDING_UP: { label: "trending up", tone: "good" },
  TRENDING_DOWN: { label: "trending down", tone: "bad" },
  RANGING: { label: "ranging", tone: "muted" },
  HIGH_VOLATILITY: { label: "high volatility", tone: "warn" },
};

function trendText(v: number | null | undefined) {
  if (v == null) return DASH;
  return v > 0.3 ? "up" : v < -0.3 ? "down" : "flat";
}

function Instruments({ rows, market }: { rows: Any[]; market: string }) {
  return (
    <Table rows={rows} empty="No instruments." columns={[
      { key: "symbol", label: "Instrument" },
      { key: "price", label: "Price", numeric: true, render: (x: Any) => x.insufficient_history ? <span className="text-xs text-muted">no stored bars yet</span> : num(x.price, market === "forex" ? 5 : 2) },
      { key: "regime", label: "Regime", render: (x: Any) => x.regime ? <Badge tone={REGIME[x.regime]?.tone ?? "muted"}>{REGIME[x.regime]?.label ?? x.regime}</Badge> : DASH },
      { key: "trend", label: "Trend 1h / 4h / 1d", render: (x: Any) => ["1h", "4h", "1d"].map((tf) => trendText(x.trend_by_timeframe?.[tf])).join(" / ") },
      { key: "rsi", label: "RSI", numeric: true, render: (x: Any) => num(x.momentum?.rsi, 0) },
      { key: "adx", label: "ADX", numeric: true, render: (x: Any) => num(x.trend_strength?.adx, 0) },
      { key: "atr", label: "ATR % of price", numeric: true, render: (x: Any) => pct(x.volatility?.atr_pct, 2) },
      { key: "vwap", label: "From VWAP (ATR)", numeric: true, render: (x: Any) => num(x.vwap?.distance_atr, 2) },
      { key: "sr", label: "Support / resistance", render: (x: Any) => `${num(x.levels?.support, market === "forex" ? 5 : 2)} / ${num(x.levels?.resistance, market === "forex" ? 5 : 2)}` },
      ...(market === "forex" ? [
        { key: "session", label: "Session", render: (x: Any) => x.session },
        { key: "spread", label: "Spread / ATR", numeric: true, render: (x: Any) => num(x.liquidity?.spread_vs_atr, 2) },
      ] : [
        { key: "qv", label: "24h volume (USDT)", numeric: true, render: (x: Any) => num(x.liquidity?.quote_volume_24h, 0) },
      ]),
      { key: "event", label: "Event risk", render: (x: Any) => x.event_risk?.blocked ? <Badge tone="warn">entries paused: {x.event_risk.active?.[0]?.title}</Badge> : x.event_risk?.upcoming?.[0] ? <span className="text-xs">next: {x.event_risk.upcoming[0].title}, {time(x.event_risk.upcoming[0].start)}</span> : <span className="text-xs text-muted">none in 7 days</span> },
      { key: "age", label: "Data", render: (x: Any) => x.as_of ? `${num(x.data_age_minutes, 0)} min old` : DASH },
    ]} />
  );
}

export default function MarketIntelligencePage() {
  const { data, error, loading } = useApi<Any>("/intelligence/markets", 60_000);
  return (
    <>
      <PageHeader title="Market intelligence" subtitle="Forex and crypto side by side: trend, momentum, volatility, VWAP, levels, liquidity, session and scheduled events" />
      <Loading error={error} loading={loading && !data} />
      {data && (
        <div className="space-y-4">
          <p className="rounded-lg border border-line bg-elevated p-3 text-sm">
            These are measurements from closed price bars that NEXUS stores, not trade signals. Machine-learning filters were tested on the forex modules and could not tell winning trades from losing ones (accuracy no better than chance), so none is used. New entries pause around scheduled central-bank decisions and US data releases.
          </p>
          <Card title="Crypto (Binance)"><Instruments rows={data.crypto ?? []} market="crypto" /></Card>
          <Card title="Forex and gold (Exness)"><Instruments rows={data.forex ?? []} market="forex" /></Card>
          <Card title="Scheduled events, next 7 days">
            <Table rows={data.upcoming_events ?? []} empty="No scheduled high-impact events in the next 7 days." columns={[
              { key: "start", label: "When", render: (x: Any) => x.time_known ? time(x.start) : `${time(x.start)} (whole day: time not published)` },
              { key: "currency", label: "Currency" },
              { key: "title", label: "Event" },
              { key: "window", label: "New entries paused", render: (x: Any) => `${time(x.blackout_from)} – ${time(x.blackout_to)}` },
            ]} />
            <details className="mt-2 text-xs text-muted">
              <summary className="cursor-pointer">Sources and coverage</summary>
              <ul className="mt-1 space-y-1">
                {Object.entries(data.calendar_sources ?? {}).map(([k, v]: [string, Any]) => (
                  <li key={k}>{v.title}: complete until {v.covers_until} (<a className="underline" href={v.url} target="_blank" rel="noreferrer">official page</a>). {v.note}</li>
                ))}
              </ul>
            </details>
          </Card>
        </div>
      )}
    </>
  );
}
