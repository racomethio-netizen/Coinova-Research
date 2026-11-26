"use client";

import { Badge, Card, Loading, PageHeader, Stat, Table } from "@/components/ui";
import { DASH, num, pct, time } from "@/lib/format";
import { useApi } from "@/lib/hooks";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

function Rebalancing() {
  const { data: r } = useApi<Any>("/research/rebalancing", 60_000);
  const bt = r?.backtest;
  if (!bt) return null;
  const f = r.forward;
  const rows = Object.entries(bt.rebalancing ?? {}).map(([k, v]: [string, Any]) => ({ key: k, ...v }));
  const mom = Object.entries(bt.xsmom ?? {}).map(([k, v]: [string, Any]) => ({ key: k, ...v }));
  return (
    <>
      <Card title="Rebalancing bot vs holding the same coins (Jan 2024 – Oct 2026)" actions={<Badge tone={bt.summary?.rebalancing_passed?.length ? "good" : "bad"}>{bt.summary?.rebalancing_passed?.length ? `${bt.summary.rebalancing_passed.length} passed` : "none passed"}</Badge>}>
        <Table rows={rows} empty="No result." columns={[
          { key: "key", label: "Basket : rule" },
          { key: "s", label: "Rebalanced", numeric: true, render: (x: Any) => pct(x.strategy?.net_return, 1) },
          { key: "h", label: "Just held", numeric: true, render: (x: Any) => pct(x.buy_and_hold?.net_return, 1) },
          { key: "dd", label: "Worst drop", numeric: true, render: (x: Any) => pct(x.strategy?.max_drawdown, 1) },
          { key: "n", label: "Rebalances", numeric: true, render: (x: Any) => x.rebalances },
          { key: "v", label: "Verdict", render: (x: Any) => x.key.endsWith(":weekly") ? <span className="text-xs text-muted">reported only</span> : <Badge tone={x.passed ? "good" : "bad"}>{x.passed ? "passed" : "failed"}</Badge> },
        ]} />
        <p className="mt-2 text-xs text-muted">Passing means it beat holding the same basket after costs. It still carries the full market risk: its worst drop was about half the money, like holding.</p>
      </Card>
      <Card title="Rebalancing bot: paper forward test (BTC 40% / ETH 40% / USDT 20%)">
        {f?.days ? (
          <p className="text-sm">Started {time(f.started_at)} with {num(f.initial, 0)} paper USDT: now {num(f.equity, 2)} ({pct(f.return_since_start, 2)}); holding the same basket would be {num(f.hold_equity, 2)} ({pct(f.hold_return, 2)}). {f.rebalances?.length ?? 0} rebalances so far.</p>
        ) : <p className="text-sm">{f?.message ?? "Waiting for the first closed day."}</p>}
      </Card>
      <Card title="Cross-sectional momentum (top 3 of 10 coins, weekly)" actions={<Badge tone={bt.summary?.xsmom_passed?.length ? "good" : "bad"}>{bt.summary?.xsmom_passed?.length ? "passed" : "none passed"}</Badge>}>
        <Table rows={mom} empty="No result." columns={[
          { key: "key", label: "Variant" },
          { key: "r", label: "Net", numeric: true, render: (x: Any) => pct(x.strategy?.net_return, 1) },
          { key: "sh", label: "Sharpe", numeric: true, render: (x: Any) => num(x.strategy?.sharpe, 2) },
          { key: "dd", label: "Worst drop", numeric: true, render: (x: Any) => pct(x.strategy?.max_drawdown, 1) },
          { key: "p", label: "p-value", numeric: true, render: (x: Any) => num(x.p_value, 3) },
          { key: "why", label: "Failed checks", render: (x: Any) => <span className="text-xs text-muted">{Object.entries(x.gates ?? {}).filter(([, ok]) => !ok).map(([g]) => g).join(", ") || DASH}</span> },
        ]} />
        <p className="mt-2 text-xs text-muted">Holding BTC over the same period: {pct(bt.btc_hold?.net_return, 1)} (worst drop {pct(bt.btc_hold?.max_drawdown, 1)}). Rules written before the run: {r.preregistration}</p>
      </Card>
    </>
  );
}

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
          <Rebalancing />
          <p className="text-xs text-muted">Generated {time(r.generated_at)} by {r.engine} from {r.source}. A strategy passes only if it made money, had a profit factor of at least 1.2, never fell more than 25%, made at least 50 trades, was positive in 2 of 3 years, stayed profitable at double fees, and its edge was statistically significant after correcting for testing many strategies at once.</p>
        </div>
      )}
    </>
  );
}
