"use client";

import { Badge, Card, Loading, PageHeader, Stat, Table } from "@/components/ui";
import { DASH, num, pct, time } from "@/lib/format";
import { useApi } from "@/lib/hooks";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

const TFS = ["1h", "4h", "1d"];

export default function StrategyLab() {
  const { data: r, error, loading } = useApi<Any>("/research/library", 60_000);
  const fwd = r?.forward;
  const rows: Any[] = r
    ? Object.keys(r.strategies).flatMap((code) =>
        TFS.map((tf) => {
          const key = `${code}@${tf}`;
          return { key, code, tf, family: r.strategies[code].family, bt: r.backtest?.[key] ?? {}, fw: fwd?.strategies?.[key] ?? {}, st: r.status?.strategies?.[key] ?? {} };
        }))
    : [];
  const passed = rows.filter((x) => x.bt.passed);
  const open = rows.flatMap((x) => (x.st.open_positions ?? []).map((p: Any) => ({ ...p, key: x.key })));
  return (
    <>
      <PageHeader title="Strategy lab" subtitle="Eight public strategy types (TradingView, Freqtrade and Turtle templates) tested once with fees, spread and slippage, then followed on new market data. SIMULATED with virtual money: no orders are placed." />
      <div role="status" className="mb-4 rounded border border-down/40 bg-down/10 px-3 py-2 text-sm font-semibold text-down">LIVE TRADING DISABLED · simulated balances only, never real exchange balances</div>
      <Loading error={error} loading={loading && !r} />
      {r && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label="Backtests passed" value={`${passed.length} of ${rows.length}`} hint="pre-registered gates, Holm-corrected" />
            <Stat label="Paper days recorded" value={`${fwd?.days_elapsed ?? 0} of ${fwd?.days_required ?? 180}`} hint={`started ${time(fwd?.started_at).slice(0, 10)}`} />
            <Stat label="Open simulated positions" value={String(open.length)} />
            <Stat label="Data gaps logged" value={String(fwd?.gap_events ?? 0)} tone={(fwd?.gap_events ?? 0) > 0 ? "warn" : "flat"} />
          </div>
          <Card title="Backtest and paper results by strategy">
            <Table rows={rows} empty="No strategies." columns={[
              { key: "code", label: "Strategy", render: (x: Any) => <span>{x.code.replaceAll("_", " ")} <span className="text-muted">({x.family.replace("_", " ")})</span></span> },
              { key: "tf", label: "Bars" },
              { key: "verdict", label: "Backtest", render: (x: Any) => <Badge tone={x.bt.passed ? "good" : "bad"}>{x.bt.passed ? "passed" : "failed"}</Badge> },
              { key: "bt_pf", label: "Profit factor", numeric: true, render: (x: Any) => (x.bt.profit_factor == null ? DASH : num(x.bt.profit_factor, 2)) },
              { key: "bt_ret", label: "Backtest return", numeric: true, render: (x: Any) => (x.bt.return_pct == null ? DASH : pct(x.bt.return_pct, 1)) },
              { key: "bt_n", label: "Trades", numeric: true, render: (x: Any) => String(x.bt.trades ?? 0) },
              { key: "fw_n", label: "Paper trades", numeric: true, render: (x: Any) => String(x.fw.trades ?? 0) },
              { key: "fw_pnl", label: "Paper P&L", numeric: true, render: (x: Any) => (x.fw.net_pnl == null ? DASH : `${num(x.fw.net_pnl)} USDT`) },
              { key: "fw_dd", label: "Paper drop", numeric: true, render: (x: Any) => (x.fw.max_drawdown == null ? DASH : pct(x.fw.max_drawdown, 1)) },
              { key: "ready", label: "Data", render: (x: Any) => {
                const w = Object.values(x.st.warm ?? {}) as string[];
                const ready = w.length > 0 && w.every((v) => v === "ready");
                return <Badge tone={ready ? "good" : "warn"}>{w.length === 0 ? "waiting" : ready ? "ready" : "warming up"}</Badge>;
              } },
            ]} />
            <p className="mt-2 text-xs text-muted">Backtest: 1h and 4h on 10 coins (2023-2026), daily on BTC and ETH (2019-2026). Holding the coins returned about +108% (10 coins) and +1,599% (BTC and ETH) over those periods. A pass is not a promise of profit. Rules: {r.preregistration}.</p>
          </Card>
          <Card title="Open simulated positions">
            <Table rows={open} empty="No open simulated positions." columns={[
              { key: "key", label: "Strategy" },
              { key: "symbol", label: "Coin", render: (p: Any) => p.symbol.replace("USDT", "") },
              { key: "entry_time", label: "Opened", render: (p: Any) => time(p.entry_time) },
              { key: "entry", label: "Entry", numeric: true, render: (p: Any) => num(p.entry) },
              { key: "stop", label: "Stop", numeric: true, render: (p: Any) => num(p.stop) },
              { key: "target", label: "Target", numeric: true, render: (p: Any) => num(p.target) },
              { key: "qty", label: "Size", numeric: true, render: (p: Any) => num(p.qty, 6) },
              { key: "bars", label: "Bars held", numeric: true, render: (p: Any) => `${p.bars} of ${p.time_limit}` },
            ]} />
          </Card>
          <Card title="Paper P&L by month (all strategies, simulated)">
            <Table rows={rows.filter((x) => (x.fw.monthly ?? []).length > 0).flatMap((x) => x.fw.monthly.map((m: Any) => ({ ...m, key: x.key })))} empty="No completed paper trades yet." columns={[
              { key: "key", label: "Strategy" },
              { key: "period", label: "Month" },
              { key: "pnl", label: "P&L", numeric: true, render: (m: Any) => `${num(m.pnl)} USDT` },
            ]} />
            <p className="mt-2 text-xs text-muted">Daily and weekly figures are in the API response. Each strategy and timeframe has its own 10,000 virtual USDT. The 180 days count calendar days since the start; nothing is reported as complete before then.</p>
          </Card>
        </div>
      )}
    </>
  );
}
