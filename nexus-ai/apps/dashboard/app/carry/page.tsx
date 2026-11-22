"use client";

import { Badge, Card, Loading, PageHeader, Stat, Table } from "@/components/ui";
import { num, pct, time } from "@/lib/format";
import { useApi } from "@/lib/hooks";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

function EquityLine({ series }: { series: { t: string; equity: string }[] }) {
  if (series.length < 2) return <p className="text-sm text-muted">The curve appears after the second hourly mark.</p>;
  const W = 640, H = 180, P = 36;
  const ys = series.map((p) => Number(p.equity));
  const lo = Math.min(...ys), hi = Math.max(...ys);
  const span = hi - lo || 1;
  const x = (i: number) => P + (i / (series.length - 1)) * (W - P - 8);
  const y = (v: number) => 8 + (1 - (v - lo) / span) * (H - 28);
  const d = series.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(Number(p.equity)).toFixed(1)}`).join("");
  return (
    <div className="overflow-x-auto">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full min-w-[320px]" role="img"
        aria-label={`Paper equity from ${num(lo)} to ${num(hi)} USDT over ${series.length} hourly marks`}>
        <line x1={P} x2={W - 8} y1={y(hi)} y2={y(hi)} stroke="var(--color-line)" />
        <line x1={P} x2={W - 8} y1={y(lo)} y2={y(lo)} stroke="var(--color-line)" />
        <text x={2} y={y(hi) + 4} fontSize="10" fill="var(--color-muted)">{num(hi, 0)}</text>
        <text x={2} y={y(lo) + 4} fontSize="10" fill="var(--color-muted)">{num(lo, 0)}</text>
        <path d={d} fill="none" stroke="var(--color-accent)" strokeWidth="2" />
        <circle cx={x(series.length - 1)} cy={y(ys[ys.length - 1])} r="3" fill="var(--color-accent)" />
      </svg>
    </div>
  );
}

function Gate({ label, value, ok, target }: { label: string; value: string; ok: boolean | null; target: string }) {
  return (
    <li className="flex flex-wrap items-center justify-between gap-2 border-b border-line py-2 last:border-0">
      <span>{label}</span>
      <span className="flex items-center gap-2"><span className="num">{value}</span><span className="text-xs text-muted">needs {target}</span>
        <Badge tone={ok == null ? "muted" : ok ? "good" : "warn"}>{ok == null ? "too early" : ok ? "on track" : "below"}</Badge></span>
    </li>
  );
}

export default function CarryTest() {
  const { data, error, loading } = useApi<Any>("/research/carry-paper", 60_000);
  const r = data;
  const weeksShare = r?.complete_weeks ? r.positive_weeks / r.complete_weeks : null;
  return (
    <>
      <PageHeader title="Funding carry test" subtitle="Forward paper test: long spot + short perpetual on BTC and ETH with real Binance prices and real funding. Pre-registered rules; one decision at day 90. Paper only, nothing is executed." />
      <Loading error={error} loading={loading && !data} />
      {r && !r.started && <p className="rounded-lg border border-line bg-elevated p-3 text-sm">Waiting for the first hourly run to open the paper position.</p>}
      {r?.started && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label="Paper equity (exit value)" value={num(r.equity)} hint={`started with ${num(r.capital, 0)} USDT`} />
            <Stat label="Return so far" value={pct(r.return, 3)} tone={Number(r.return) > 0 ? "up" : Number(r.return) < 0 ? "down" : "flat"} hint={`day ${num(r.days, 1)} of 90`} />
            <Stat label="Funding received" value={num(r.funding_received)} hint={`costs paid ${num(r.costs_paid)}`} />
            <Stat label="Decision date" value={time(r.decision_at).slice(0, 10)} hint={r.decision === "due" ? "decision due" : "pending"} />
          </div>
          <Card title="Equity (hourly, at exit prices)"><EquityLine series={r.series} /></Card>
          <Card title="Pass thresholds (judged once at day 90)">
            <ul className="text-sm">
              <Gate label="Annualised net return" value={r.annualised == null ? "after day 7" : pct(r.annualised, 2)} ok={r.annualised == null ? null : Number(r.annualised) >= 0.04} target=">= 4%" />
              <Gate label="Maximum drawdown" value={pct(r.max_drawdown, 3)} ok={Number(r.max_drawdown) <= 0.02} target="<= 2%" />
              <Gate label="Positive weeks" value={r.complete_weeks ? `${r.positive_weeks} of ${r.complete_weeks}` : "no complete week yet"} ok={weeksShare == null ? null : weeksShare >= 0.6} target=">= 60%" />
              <Gate label="Data gaps" value={pct(r.gap_share, 1)} ok={r.gap_share < 0.05} target="< 5%" />
            </ul>
            <p className="mt-2 text-xs text-muted">{r.note} Rules: {r.preregistration}.</p>
          </Card>
          <Card title="Funding, rebalances and gaps">
            <Table rows={[...(r.recent_events ?? [])].reverse()} empty="No events yet." columns={[
              { key: "at", label: "Time", render: (e: Any) => time(e.at) },
              { key: "symbol", label: "Symbol" },
              { key: "kind", label: "Event", render: (e: Any) => <Badge tone={e.kind === "gap" ? "warn" : e.kind === "funding" ? "good" : "info"}>{e.kind}</Badge> },
              { key: "detail", label: "Detail", render: (e: Any) => <span className="text-xs text-muted">{e.kind === "funding" ? `rate ${pct(e.detail.rate, 4)} · ${Number(e.detail.payment) >= 0 ? "received" : "paid"} ${num(Math.abs(Number(e.detail.payment)), 4)} USDT` : e.kind === "rebalance" ? `cost ${num(e.detail.cost, 4)} USDT` : e.kind === "gap" ? e.detail.reason : "position opened"}</span> },
            ]} />
          </Card>
          <p className="text-xs text-muted">Not executable with real money today: the Binance account has no futures access.</p>
        </div>
      )}
    </>
  );
}
