"use client";

import { Badge, Card, Loading, PageHeader, Stat, Table } from "@/components/ui";
import { DASH, num, pct, time } from "@/lib/format";
import { useApi } from "@/lib/hooks";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

const REGIME_TONE: Record<string, "good" | "warn" | "bad" | "muted"> = {
  TRENDING_UP: "good", RANGING: "muted", TRENDING_DOWN: "bad", HIGH_VOLATILITY: "warn",
  LOW_LIQUIDITY: "bad", BTC_RISK_OFF: "bad",
};
const STRATEGY: Record<string, string> = { trend_pullback: "Trend Pullback", breakout_retest_v2: "Breakout + Retest" };

function activeStrategy(regime: string | undefined): string {
  if (regime === "TRENDING_UP") return "Trend Pullback (breakouts also allowed)";
  if (regime === "HIGH_VOLATILITY") return "Trend Pullback / Breakout at half risk";
  if (regime === "RANGING") return "Breakout only (needs a confirmed breakout)";
  if (regime === "TRENDING_DOWN" || regime === "BTC_RISK_OFF" || regime === "LOW_LIQUIDITY") return "None: entries blocked";
  return DASH;
}

function Checks({ checks }: { checks: Record<string, boolean> | undefined }) {
  if (!checks) return <span className="text-muted">{DASH}</span>;
  return (
    <ul className="flex flex-wrap gap-1">
      {Object.entries(checks).map(([k, v]) => (
        <li key={k}><Badge tone={v ? "good" : "bad"}>{v ? "✓" : "✗"} {k.replaceAll("_", " ")}</Badge></li>
      ))}
    </ul>
  );
}

export default function SpotEngine() {
  const { data: r, error, loading } = useApi<Any>("/spot-engine", 30_000);
  const st = r?.status;
  const risk = st?.risk ?? {};
  const v = r?.validation;
  return (
    <>
      <PageHeader title="Spot strategy engine" subtitle="Trend Pullback and Breakout + Retest on Binance spot, with a market-regime filter and strict risk rules. Paper trading on real prices only; every signal is recorded with the reason it was taken or rejected." />
      <div role="status" className="mb-4 rounded-lg border border-down/50 bg-down/10 p-3 text-center text-sm font-semibold text-down">
        LIVE TRADING DISABLED. This engine only places simulated paper orders with virtual money.
      </div>
      <Loading error={error} loading={loading && !r} />
      {r && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label="Paper equity" value={risk.equity == null ? DASH : `${num(risk.equity)} USDT`} hint={`started ${r.portfolio?.started_at ? time(r.portfolio.started_at).slice(0, 10) : DASH} with ${num(r.portfolio?.initial_capital)} virtual USDT`} />
            <Stat label="Net P&L (closed)" value={r.performance ? `${num(r.performance.net_pnl)} USDT` : DASH} tone={r.performance?.net_pnl > 0 ? "up" : r.performance?.net_pnl < 0 ? "down" : "flat"} hint={`fees ${num(r.performance?.fees ?? 0)} USDT`} />
            <Stat label="Today / this week drawdown" value={`${pct(Math.max(0, risk.day_drawdown_pct ?? 0), 2)} / ${pct(Math.max(0, risk.week_drawdown_pct ?? 0), 2)}`} hint="limits 2% / 4%" />
            <Stat label="Open risk" value={risk.open_risk_pct == null ? DASH : pct(risk.open_risk_pct, 2)} hint={`${risk.open_positions ?? 0} of 3 positions · limit 1%`} />
          </div>

          {r.backtest && (
            <Card title="Backtest verdict (pre-registered, 10 coins, Nov 2022 - Nov 2026, after costs)">
              <ul className="space-y-1 text-sm">
                {["trend_pullback", "breakout_retest_v2"].map((k) => (
                  <li key={k} className="flex flex-wrap justify-between gap-2">
                    <span>{STRATEGY[k]}</span>
                    <span className="flex items-center gap-2">
                      <Badge tone={r.backtest[k].result === "PASS" ? "good" : "bad"}>{r.backtest[k].result === "PASS" ? "passed" : "failed"}</Badge>
                      <span className="num text-xs text-muted">{r.backtest[k].trades} trades · profit factor {num(r.backtest[k].profit_factor)} · {pct(r.backtest[k].return_pct, 1)} · max drop {pct(r.backtest[k].max_drawdown, 1)}</span>
                    </span>
                  </li>
                ))}
              </ul>
              <p className="mt-2 text-xs text-muted">Both lost money after fees in every period and on 8-9 of 10 coins (holding BTC returned {pct(r.backtest.btc_buy_and_hold_return_pct, 0)}). They keep running in paper only so their behaviour on new data is visible; they are not eligible for live trading. Details: {r.backtest.source}.</p>
            </Card>
          )}

          <Card title="Market regime and active strategy">
            {st?.symbols ? (
              <Table rows={Object.entries(st.symbols).map(([sym, x]: [string, Any]) => ({ sym, ...x }))} empty="No data." columns={[
                { key: "sym", label: "Pair" },
                { key: "regime", label: "Regime", render: (x: Any) => x.regime ? <Badge tone={REGIME_TONE[x.regime] ?? "muted"}>{x.regime.replaceAll("_", " ")}</Badge> : (x.note ?? DASH) },
                { key: "strategy", label: "Active strategy", render: (x: Any) => activeStrategy(x.regime) },
                { key: "btc", label: "BTC trend", render: (x: Any) => x.btc_risk_off ? <Badge tone="bad">risk-off</Badge> : <Badge tone="good">ok</Badge> },
                { key: "adx", label: "1h ADX", numeric: true, render: (x: Any) => num(x.breakdown?.h1_adx, 1) },
                { key: "trend", label: "Filters", render: (x: Any) => x.breakdown ? `4h>EMA200 ${x.breakdown.h4_above_ema200 ? "yes" : "no"} · 1h>EMA200 ${x.breakdown.h1_above_ema200 ? "yes" : "no"} · EMA50>200 ${x.breakdown.h1_ema50_above_200 ? "yes" : "no"} · structure ${x.breakdown.h1_structure}` : DASH },
                { key: "fresh", label: "Data", render: (x: Any) => <Badge tone={x.fresh ? "good" : "bad"}>{x.fresh ? "fresh" : "stale"}</Badge> },
              ]} />
            ) : <p className="text-sm text-muted">Waiting for the first 15-minute cycle.</p>}
            {st && <p className="mt-2 text-xs text-muted">Last cycle {time(st.at)} · kill switch {st.kill_switch ? "ENGAGED" : "off"}</p>}
          </Card>

          <Card title="Signals: taken and rejected (newest first)">
            <Table rows={r.signals} empty="No candidate setups yet. Candidates appear only when the core pattern forms; most bars have none." columns={[
              { key: "bar_time", label: "Bar", render: (x: Any) => time(x.bar_time) },
              { key: "symbol", label: "Pair" },
              { key: "strategy", label: "Strategy", render: (x: Any) => STRATEGY[x.strategy] ?? x.strategy },
              { key: "status", label: "Decision", render: (x: Any) => <Badge tone={x.status === "executed" ? "good" : "muted"}>{x.status === "executed" ? "taken" : "rejected"}</Badge> },
              { key: "levels", label: "Entry / stop / TP1 / TP2", render: (x: Any) => `${num(x.entry)} / ${num(x.stop)} / ${num(x.details?.tp1)} / ${num(x.target)}` },
              { key: "score", label: "Score", numeric: true, render: (x: Any) => pct(x.score, 0) },
              { key: "reason", label: "Why", render: (x: Any) => x.reason ?? "all checks passed" },
              { key: "checks", label: "Checks", render: (x: Any) => <Checks checks={x.details?.checks} /> },
            ]} />
          </Card>

          <Card title="Open paper positions">
            <Table rows={r.open_positions} empty="No open paper positions." columns={[
              { key: "symbol", label: "Pair" },
              { key: "opened_at", label: "Opened", render: (x: Any) => time(x.opened_at) },
              { key: "quantity", label: "Quantity", numeric: true, render: (x: Any) => num(x.quantity, 6) },
              { key: "entry", label: "Entry", numeric: true, render: (x: Any) => num(x.entry) },
              { key: "stop", label: "Stop", numeric: true, render: (x: Any) => num(x.stop) },
              { key: "target", label: "TP2", numeric: true, render: (x: Any) => num(x.target) },
              { key: "realized_pnl", label: "Realized (partials)", numeric: true, render: (x: Any) => num(x.realized_pnl) },
            ]} />
          </Card>

          <Card title="Performance and 180-day validation">
            {r.performance ? (
              <dl className="grid grid-cols-2 gap-2 text-sm md:grid-cols-4">
                <div><dt className="text-xs text-muted">Closed trades</dt><dd className="num">{r.performance.closed_trades}</dd></div>
                <div><dt className="text-xs text-muted">Win rate</dt><dd className="num">{pct(r.performance.win_rate, 0)}</dd></div>
                <div><dt className="text-xs text-muted">Profit factor</dt><dd className="num">{num(r.performance.profit_factor)}</dd></div>
                <div><dt className="text-xs text-muted">Expectancy / trade</dt><dd className="num">{num(r.performance.expectancy)} USDT</dd></div>
              </dl>
            ) : <p className="text-sm text-muted">No paper trades yet.</p>}
            {v && (
              <ul className="mt-3 space-y-1 text-sm">
                <li className="flex justify-between"><span>Paper trading duration</span><Badge tone={v.checks.days ? "good" : "muted"}>{v.days} of {v.required_days} days</Badge></li>
                <li className="flex justify-between"><span>Completed trades</span><Badge tone={v.checks.trades ? "good" : "muted"}>{v.trades} of {v.required_trades}</Badge></li>
                <li className="flex justify-between"><span>Profit factor at least {v.profit_factor_min}</span><Badge tone={v.checks.profit_factor ? "good" : "muted"}>{v.checks.profit_factor ? "met" : "not yet"}</Badge></li>
                <li className="flex justify-between"><span>Positive after fees</span><Badge tone={v.checks.expectancy_positive ? "good" : "muted"}>{v.checks.expectancy_positive ? "yes" : "not yet"}</Badge></li>
              </ul>
            )}
            <p className="mt-2 text-xs text-muted">{v?.note ?? "Validation starts with the first paper day."} Targets are not guarantees: no strategy is guaranteed to make money.</p>
          </Card>
        </div>
      )}
    </>
  );
}
