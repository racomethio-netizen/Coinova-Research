"use client";

import { Badge, Card, Loading, PageHeader, Stat, Table } from "@/components/ui";
import { DASH, num, pct, signed, time } from "@/lib/format";
import { useApi, useMode } from "@/lib/hooks";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

export default function Performance() {
  const { mode } = useMode();
  const { data, error, loading } = useApi<Any>(`/performance?mode=${mode}`, 60000);
  const r = data?.report;
  return (
    <>
      <PageHeader title="Performance analytics" subtitle={r?.title ?? "No portfolio yet"} />
      <Loading error={error} loading={loading && !data} />
      {data && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label="Realized P&L" value={num(r?.realized_pnl)} tone={signed(r?.realized_pnl)} />
            <Stat label="Win rate" value={pct(r?.win_rate, 1)} />
            <Stat label="Profit factor" value={num(r?.profit_factor)} />
            <Stat label="Max drawdown" value={pct(r?.max_drawdown)} />
            <Stat label="Closed trades" value={String(r?.closed_trades ?? 0)} />
            <Stat label="Fees paid" value={num(r?.fees_paid)} />
            <Stat label="Avg slippage" value={r?.avg_slippage_bps ? `${num(r.avg_slippage_bps)} bps` : DASH} />
          </div>
          <Card title="Independent validation (walk-forward, out-of-sample, real data, after costs)">
            <p className="mb-3 text-xs text-muted">Only out-of-sample results count. A strategy is enabled only if every gate passes.</p>
            <Table rows={data.backtests} empty="No validation runs recorded." columns={[
              { key: "strategy", label: "Strategy" },
              { key: "range", label: "Data", render: (b: Any) => `${time(b.data_start).slice(0, 10)} → ${time(b.data_end).slice(0, 10)}` },
              { key: "n", label: "OOS trades", numeric: true, render: (b: Any) => b.summary?.n ?? DASH },
              { key: "win", label: "Win rate", numeric: true, render: (b: Any) => pct(b.summary?.win_rate, 1) },
              { key: "exp", label: "Expectancy / trade", numeric: true, render: (b: Any) => pct(b.summary?.expectancy, 3) },
              { key: "pf", label: "Profit factor", numeric: true, render: (b: Any) => num(b.summary?.profit_factor) },
              { key: "pw", label: "Positive windows", numeric: true, render: (b: Any) => pct(b.summary?.positive_window_share, 0) },
              { key: "passed", label: "Result", render: (b: Any) => <Badge tone={b.passed ? "good" : "bad"}>{b.passed ? "passed" : "rejected"}</Badge> },
            ]} />
          </Card>
        </div>
      )}
    </>
  );
}
