"use client";

import { Badge, Card, Loading, PageHeader, Stat, Table } from "@/components/ui";
import { DASH, num, pct, time } from "@/lib/format";
import { useApi } from "@/lib/hooks";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

export default function StrategyTestPage() {
  const { data: r, error, loading } = useApi<Any>("/research/freqtrade-community");
  const s = r?.summary;
  const b = r?.benchmarks;
  return (
    <>
      <PageHeader title="Strategy test" subtitle="Public Freqtrade community strategies, run unchanged by Freqtrade on real Binance data they were not built on, with real fees" />
      <Loading error={error} loading={loading && !r} />
      {r && (
        <div className="space-y-4">
          <p className="rounded-lg border border-line bg-elevated p-3 text-sm">
            Historical backtest, {r.window?.[0]?.slice(0, 4)}-{r.window?.[0]?.slice(4, 6)} to {r.window?.[1]?.slice(0, 4)}-10, {r.pairs?.length} coins, fees {pct(r.fees?.[0], 1)} per side (stress {pct(r.fees?.[1], 1)}). No order was placed. Rules were written down before the run ({r.preregistration}).
          </p>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Stat label="Strategies tested" value={s?.tested ?? DASH} hint={`${s?.excluded ?? 0} not eligible, ${s?.errors ?? 0} would not load`} />
            <Stat label="Made money (before checks)" value={s?.profitable ?? DASH} tone="flat" />
            <Stat label="Passed every check" value={s?.passed ?? DASH} tone={s?.passed ? "up" : "warn"} />
            <Stat label="Holding BTC over the same time" value={pct(b?.btc_hold, 1)} hint={`all 10 coins held equally: ${pct(b?.equal_weight_hold, 1)}`} />
          </div>
          <Card title="Ranked by net result">
            <Table rows={(r.ranked ?? []).map((x: Any, i: number) => ({ ...x, rank: i + 1 }))} empty="No strategies tested." columns={[
              { key: "rank", label: "#", numeric: true },
              { key: "strategy", label: "Strategy" },
              { key: "timeframe", label: "Bars" },
              { key: "net", label: "Net result", numeric: true, render: (x: Any) => <span className={x.stats.net_return > 0 ? "text-up" : "text-down"}>{pct(x.stats.net_return, 1)}</span> },
              { key: "dd", label: "Worst drop", numeric: true, render: (x: Any) => pct(x.stats.max_drawdown, 1) },
              { key: "pf", label: "Profit factor", numeric: true, render: (x: Any) => num(x.stats.profit_factor, 2) },
              { key: "trades", label: "Trades", numeric: true, render: (x: Any) => x.stats.trades },
              { key: "fees2", label: "At 2x fees", numeric: true, render: (x: Any) => pct(x.stress_2x_fees?.net_return, 1) },
              { key: "verdict", label: "Verdict", render: (x: Any) => <Badge tone={x.passed ? "good" : "bad"}>{x.passed ? "passed" : "failed"}</Badge> },
              { key: "why", label: "Why it failed", render: (x: Any) => <span className="text-xs text-muted">{x.gate_failures?.join("; ") || DASH}</span> },
            ]} />
          </Card>
          <Card title="Not tested">
            <Table rows={r.not_tested} empty="Every strategy was tested." columns={[
              { key: "strategy", label: "Strategy" },
              { key: "file", label: "File" },
              { key: "reason", label: "Reason", render: (x: Any) => <span className="text-xs">{x.excluded || x.error || DASH}</span> },
            ]} />
          </Card>
          <p className="text-xs text-muted">Generated {time(r.generated_at)} by {r.engine} from {r.source}. A strategy passes only if it made money, had a profit factor of at least 1.2, never fell more than 25%, made at least 50 trades, was positive in 2 of 3 years, stayed profitable at double fees, and its edge was statistically significant after correcting for testing many strategies at once.</p>
        </div>
      )}
    </>
  );
}
