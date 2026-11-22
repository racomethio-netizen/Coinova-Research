"use client";

import { Badge, Card, Loading, PageHeader, Stat, Table } from "@/components/ui";
import { DASH, num, pct, time } from "@/lib/format";
import { useApi } from "@/lib/hooks";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

export default function TakerTest() {
  const { data: r, error, loading } = useApi<Any>("/research/taker-forward", 60_000);
  const st = r?.status;
  return (
    <>
      <PageHeader title="Taker flow test" subtitle="Forward paper test: buy when aggressive buyers dominate, only in sideways (range) markets, BTC and ETH. Frozen research rules and real Binance data; nothing is executed. Decision once, at 30 completed trades." />
      <Loading error={error} loading={loading && !r} />
      {r && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label="Completed trades" value={`${r.trades} of ${r.target_trades}`} hint="needed before any decision" />
            <Stat label="Average result / trade" value={r.mean_return == null ? DASH : pct(r.mean_return, 3)} tone={r.mean_return > 0 ? "up" : r.mean_return < 0 ? "down" : "flat"} hint="after fees and slippage" />
            <Stat label="Win rate" value={r.win_rate == null ? DASH : pct(r.win_rate, 0)} />
            <Stat label="Profit factor" value={r.profit_factor == null ? DASH : num(r.profit_factor)} />
          </div>
          <Card title="Right now">
            {st ? (
              <ul className="space-y-2 text-sm">
                {Object.entries(st.symbols as Record<string, Any>).map(([sym, v]) => (
                  <li key={sym} className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-medium">{sym}</span>
                    {v.ok ? (
                      <span className="flex flex-wrap items-center gap-2">
                        <Badge tone={v.regime_now === "range" ? "good" : "muted"}>{v.regime_now ?? "regime unknown"}</Badge>
                        <span className="text-xs text-muted">{v.regime_now === "range" ? "test is active for this market type" : "not sideways: the test waits"}</span>
                        <span className="text-xs text-muted">{v.open.length} open paper trade(s)</span>
                      </span>
                    ) : <span role="alert" className="text-xs text-down">{v.error}</span>}
                  </li>
                ))}
              </ul>
            ) : <p className="text-sm text-muted">Waiting for the first hourly run.</p>}
            {st && <p className="mt-2 text-xs text-muted">Last run {time(st.at)} · data gaps so far: {r.gap_hours} hour(s)</p>}
          </Card>
          <Card title="Pass thresholds (judged once, at 30 trades or by the deadline)">
            <ul className="space-y-1 text-sm">
              <li className="flex justify-between"><span>Average result per trade</span><span className="num">{r.mean_return == null ? DASH : pct(r.mean_return, 3)} · needs &gt; 0</span></li>
              <li className="flex justify-between"><span>Profit factor</span><span className="num">{r.profit_factor == null ? DASH : num(r.profit_factor)} · needs ≥ 1.15</span></li>
              <li className="flex justify-between"><span>t-statistic</span><span className="num">{r.t_stat == null ? DASH : num(r.t_stat)} · needs ≥ 1.0</span></li>
            </ul>
            <p className="mt-2 text-xs text-muted">Expected to take about 10 to 12 months (about 2.8 sideways-market trades a month historically). Deadline {time(r.deadline).slice(0, 10)}; fewer than 30 trades by then = inconclusive. Rules: {r.preregistration}.</p>
          </Card>
          <Card title="Completed paper trades">
            <Table rows={[...(r.trades_list ?? [])].reverse()} empty="No completed trades yet. Trades only happen in sideways markets when aggressive buying is strong." columns={[
              { key: "exit_time", label: "Closed", render: (t: Any) => time(t.exit_time) },
              { key: "symbol", label: "Symbol" },
              { key: "outcome", label: "Exit", render: (t: Any) => <Badge tone={t.outcome === "target" ? "good" : t.outcome === "stop" ? "bad" : "muted"}>{t.outcome}</Badge> },
              { key: "entry", label: "Entry / exit", render: (t: Any) => `${num(t.entry)} / ${num(t.exit)}` },
              { key: "ret", label: "Result", numeric: true, render: (t: Any) => pct(t.ret, 3) },
            ]} />
          </Card>
        </div>
      )}
    </>
  );
}
