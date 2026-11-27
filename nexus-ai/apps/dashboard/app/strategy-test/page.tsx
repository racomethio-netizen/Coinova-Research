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

const GRID_ROWS: [string, string][] = [["v1_fee1", "v1 (pause)"], ["v2_fee1", "v2 (sell)"], ["v1_fee2", "v1, 2x fees"], ["v2_fee2", "v2, 2x fees"]];
const GATE_LABELS: Record<string, string> = {
  net_positive: "net above zero", profit_factor_ge_1_1: "profit factor at least 1.1", drawdown_le_15pct: "worst drop within 15%",
  positive_2_of_4_periods: "positive in 2 of 4 periods", positive_at_2x_fees: "positive at 2x fees", positive_without_best: "positive without the best coin or instrument",
  better_than_v1: "better than v1", sharpe_above_hold: "Sharpe above holding", drawdown_10pts_smaller: "worst drop 10 points smaller",
  sharpe_above_hold_both_halves: "Sharpe above holding in both halves", holds_at_2x_costs: "still better at 2x costs", sharpe_ge_0_5: "Sharpe at least 0.5",
  significant_p_lt_0_05: "statistically significant", positive_2_of_3_years: "positive in 2 of 3 years", positive_at_2x_costs: "positive at 2x costs",
  significant_holm: "statistically significant", sharpe_ge_1: "Sharpe at least 1", drawdown_le_10pct: "worst drop within 10%",
  positive_strict_fill: "positive with stricter fills",
  drawdown_le_25pct: "worst drop within 25%", positive_4_of_5_years: "positive in 4 of 5 years", beats_reversal_baseline: "better than the simple reversal",
  positive_without_top3: "positive without the 3 best coins",
  drawdown_le_20pct: "worst drop within 20%", positive_55pct_of_years: "positive in 55% of years", positive_both_halves: "positive in both halves",
  positive_60pct_of_years: "positive in 60% of years",
};
const VOL_NAMES: Record<string, string> = { BTC: "BTC", ETH: "ETH", BTC_ETH_50_50: "BTC/ETH 50/50" };

function failed(gates: Record<string, boolean> | undefined) {
  return Object.entries(gates ?? {}).filter(([, ok]) => !ok).map(([g]) => GATE_LABELS[g] ?? g.replaceAll("_", " ")).join(", ") || DASH;
}

function Batch3() {
  const { data: r } = useApi<Any>("/research/batch3", 60_000);
  const bt = r?.backtest;
  if (!bt) return null;
  const g = bt.grid_v2;
  const p = bt.pairs;
  const f = r.forward;
  const grid = GRID_ROWS.map(([k, label]) => ({ key: k, label, ...(g?.results?.[k] ?? {}) }));
  const vol = Object.entries(bt.vol_target ?? {}).map(([k, v]: [string, Any]) => ({ key: k, ...v }));
  return (
    <>
      <Card title="Grid v2 on unseen 2020-07 – 2023-12 data (10 coins, 1,000 USDT each)" actions={<Badge tone={g?.passed ? "good" : "bad"}>{g?.passed ? "passed" : "failed"}</Badge>}>
        <Table rows={grid} empty="No result." columns={[
          { key: "label", label: "Version" },
          { key: "n", label: "Net", numeric: true, render: (x: Any) => <span className={x.net_return > 0 ? "text-up" : "text-down"}>{pct(x.net_return, 1)}</span> },
          { key: "dd", label: "Worst drop", numeric: true, render: (x: Any) => pct(x.max_drawdown, 1) },
          { key: "pf", label: "Profit factor", numeric: true, render: (x: Any) => num(x.profit_factor, 2) },
          { key: "t", label: "Round trips", numeric: true, render: (x: Any) => num(x.trips, 0) },
          { key: "rc", label: "Forced sales", numeric: true, render: (x: Any) => num(x.regime_closes, 0) },
          { key: "s", label: "Stop-losses", numeric: true, render: (x: Any) => x.stops_total },
        ]} />
        <p className="mt-2 text-xs text-muted">v1 pauses new buying when the market changes character; v2 sells the coins it holds. Failed checks (v2): {failed(g?.gates)}. Selling held coins whenever the market changed character made things worse: it forced {num(g?.results?.v2_fee1?.regime_closes, 0)} sales and lost more than v1. Grids earned money in sideways and gently rising markets and lost it in falling ones, the same pattern as on 2024–2026 data.</p>
      </Card>
      <Card title="Volatility-targeted holding (Jul 2020 – Oct 2026)" actions={<Badge tone={bt.summary?.vol_target_passed?.length ? "good" : "bad"}>{bt.summary?.vol_target_passed?.length ? `${bt.summary.vol_target_passed.length} of 3 passed` : "none passed"}</Badge>}>
        <Table rows={vol} empty="No result." columns={[
          { key: "key", label: "Asset", render: (x: Any) => VOL_NAMES[x.key] ?? x.key },
          { key: "n", label: "Strategy net", numeric: true, render: (x: Any) => pct(x.target_40?.net_return, 0) },
          { key: "h", label: "Just held", numeric: true, render: (x: Any) => pct(x.hold?.net_return, 0) },
          { key: "sh", label: "Sharpe (held)", numeric: true, render: (x: Any) => `${num(x.target_40?.sharpe, 3)} (${num(x.hold?.sharpe, 3)})` },
          { key: "dd", label: "Worst drop (held)", numeric: true, render: (x: Any) => `${pct(x.target_40?.max_drawdown, 0)} (${pct(x.hold?.max_drawdown, 0)})` },
          { key: "v", label: "Verdict", render: (x: Any) => <Badge tone={x.passed ? "good" : "bad"}>{x.passed ? "passed" : "failed"}</Badge> },
          { key: "why", label: "Failed checks", render: (x: Any) => <span className="text-xs text-muted">{failed(x.gates)}</span> },
        ]} />
        <p className="mt-2 text-xs text-muted">The rule holds less of the coins when prices swing wildly (target 40% yearly volatility) and the rest in USDT. It earned less than holding, with a smaller worst drop and slightly better return per unit of risk. Its worst drop was still large.</p>
      </Card>
      <Card title="Volatility-targeted BTC/ETH 50/50: paper forward test">
        {f?.days ? (
          <p className="text-sm">Started {time(f.started_at)} with {num(f.initial, 0)} paper USDT: now {num(f.equity, 2)} ({pct(f.return_since_start, 2)}), holding {pct(f.weight, 0)} in coins. Holding the same mix fully would be {num(f.hold_equity, 2)} ({pct(f.hold_return, 2)}). {f.trades?.length ?? 0} trades so far.</p>
        ) : <p className="text-sm">{f?.message ?? "Waiting for the first closed day."}</p>}
      </Card>
      <Card title="BTC/ETH pairs trade on perpetual futures (Jan 2024 – Oct 2026)" actions={<Badge tone={p?.passed ? "good" : "bad"}>{p?.passed ? "passed" : "failed"}</Badge>}>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Stat label="Net after costs and funding" value={pct(p?.stats?.net_return, 1)} tone={p?.stats?.net_return > 0 ? "up" : "down"} hint={`at 2x costs ${pct(p?.stress_2x?.net_return, 1)}`} />
          <Stat label="Sharpe" value={num(p?.stats?.sharpe, 2)} hint={`p-value ${num(p?.p_value, 2)}`} />
          <Stat label="Trades" value={p?.trades ?? DASH} hint={`${p?.stops ?? 0} stopped out`} />
          <Stat label="Weeks the pair was linked" value={`${p?.cointegrated_refits ?? DASH} of ${p?.refits ?? DASH}`} hint="cointegrated at 5%" />
        </div>
        <p className="mt-2 text-xs text-muted">Failed checks: {failed(p?.gates)}. BTC and ETH rarely moved together tightly enough to trade, and trades lost before costs.</p>
      </Card>
      <p className="text-xs text-muted">Historical backtests, no orders placed. Rules written before the run: {r.preregistration}</p>
    </>
  );
}

function MarketMaking() {
  const { data: r } = useApi<Any>("/research/market-making", 60_000);
  const bt = r?.backtest;
  if (!bt) return null;
  const rows = Object.entries(bt.variants ?? {}).map(([k, v]: [string, Any]) => ({ key: k, ...v }));
  const passed = bt.summary?.passed ?? [];
  return (
    <Card title="Market making: buy just below, sell just above, every minute (Jan 2024 – Oct 2026)" actions={<Badge tone={passed.length ? "good" : "bad"}>{passed.length ? `${passed.length} passed` : "none passed"}</Badge>}>
      <Table rows={rows} empty="No result." columns={[
        { key: "key", label: "Market : spread" },
        { key: "f", label: "Fills a day per coin", numeric: true, render: (x: Any) => num(x.plain?.fills_per_day_per_coin, 0) },
        { key: "b", label: "Before fees (USDT)", numeric: true, render: (x: Any) => <span className={x.plain?.pnl_before_fees > 0 ? "text-up" : "text-down"}>{num(x.plain?.pnl_before_fees, 0)}</span> },
        { key: "d", label: "Per day, 10,000 book", numeric: true, render: (x: Any) => <span className={x.plain?.pnl_per_day_per_book > 0 ? "text-up" : "text-down"}>{num(x.plain?.pnl_per_day_per_book, 1)}</span> },
        { key: "e", label: "Book empty after", numeric: true, render: (x: Any) => x.plain?.days_until_book_empty ? `${num(x.plain.days_until_book_empty, 0)} days` : DASH },
        { key: "v", label: "Verdict", render: (x: Any) => <Badge tone={x.passed ? "good" : "bad"}>{x.passed ? "passed" : "failed"}</Badge> },
        { key: "why", label: "Failed checks", render: (x: Any) => <span className="text-xs text-muted">{failed(x.gates)}</span> },
      ]} />
      <p className="mt-2 text-xs text-muted">Each coin had its own 10,000 USDT paper book with orders of 500 USDT. A fill counted only when the price traded through the order, so the price had usually moved against the order the moment it filled. Every version lost money even before fees. Professional market makers win by being first in the queue, paying almost no fees and reacting in microseconds, which a small account cannot do. Perpetuals paid real funding; spot is measured against simply holding the same coins. Historical backtest, no orders placed. Rules written before the run: {r.preregistration}</p>
    </Card>
  );
}

const MODEL_NAMES: Record<string, string> = { ridge: "Model A: linear (Ridge)", xgboost: "Model B: trees (XGBoost)", baseline: "Simple 1-day reversal (comparison)" };

function StatArb() {
  const { data: r } = useApi<Any>("/research/statarb-ml", 60_000);
  const bt = r?.backtest;
  if (!bt) return null;
  const rows = [...Object.entries(bt.models ?? {}).map(([k, v]: [string, Any]) => ({ key: k, ...v })), { key: "baseline", ...bt.baseline_reversal_1d, baseline: true }];
  const passed = bt.summary?.passed ?? [];
  return (
    <Card title="Medallion-style: many signals, long 10 / short 10 coins, daily (2022 – Oct 2026)" actions={<Badge tone={passed.length ? "good" : "bad"}>{passed.length ? `${passed.length} passed` : "none passed"}</Badge>}>
      <Table rows={rows} empty="No result." columns={[
        { key: "key", label: "Model", render: (x: Any) => MODEL_NAMES[x.key] ?? x.key },
        { key: "n", label: "Net", numeric: true, render: (x: Any) => <span className={x.net_return > 0 ? "text-up" : "text-down"}>{pct(x.net_return, 1)}</span> },
        { key: "sh", label: "Sharpe", numeric: true, render: (x: Any) => num(x.sharpe, 2) },
        { key: "dd", label: "Worst drop", numeric: true, render: (x: Any) => pct(x.max_drawdown, 1) },
        { key: "pm", label: "Price gains", numeric: true, render: (x: Any) => pct(x.breakdown?.price_moves, 0) },
        { key: "tc", label: "Trading costs", numeric: true, render: (x: Any) => pct(x.breakdown?.trading_costs, 0) },
        { key: "fu", label: "Funding", numeric: true, render: (x: Any) => pct(x.breakdown?.funding, 0) },
        { key: "v", label: "Verdict", render: (x: Any) => x.baseline ? <span className="text-xs text-muted">comparison</span> : <Badge tone={x.passed ? "good" : "bad"}>{x.passed ? "passed" : "failed"}</Badge> },
        { key: "why", label: "Failed checks", render: (x: Any) => x.baseline ? DASH : <span className="text-xs text-muted">{failed(x.gates)}</span> },
      ]} />
      <p className="mt-2 text-xs text-muted">Every Binance USDT perpetual that ever traded, delisted ones included; each day the 60 most traded were eligible. 16 signals per coin, models retrained every month on the past year only, then traded on the next month they had never seen. Equal long and short, so the market's direction does not matter. Fees, slippage and real funding are included. Price gains, costs and funding are simple sums of daily results, as a share of the book. The models did find price patterns, but the coins they sold short were the ones where short sellers pay heavy funding, and trading every day cost more again. Historical backtest, no orders placed. Rules written before the run: {r.preregistration}</p>
    </Card>
  );
}

const TREND_NAMES: Record<string, string> = { tsmom_12m: "12-month momentum", sma_200: "200-day trend filter" };

function Nqs1({ n }: { n: Any }) {
  if (!n) return null;
  const res = n.result;
  const rows = Object.entries(res?.profiles ?? {}).map(([k, x]: [string, Any]) => ({ key: k, ...x.walk_forward.metrics, passed: x.walk_forward.passed, gates: x.walk_forward.gates }));
  const anyPassed = rows.some((x: Any) => x.passed);
  const badge = !res ? <Badge tone="muted">waiting for price history</Badge> : <Badge tone={anyPassed ? "good" : "bad"}>{anyPassed ? "passed" : "none passed"}</Badge>;
  return (
    <Card title="Forex NQS-1: adaptive strategy (EURUSD, GBPUSD, USDJPY, AUDUSD, USDCHF)" actions={badge}>
      {!res ? (
        <p className="text-sm">A market-regime model chooses between four setups (momentum, trend pullback, breakout, mean reversion) and stays out when the market is unclear. Every trade needs two confirmations, must cover its costs and stays within NEXUS risk limits. Its test rules were written down before any data was seen. The test waits for your broker&apos;s own price history: run the Exness bridge once with <code>--export-history</code> (see All markets → Exness bridge). No result exists yet.</p>
      ) : (
        <Table rows={rows} empty="No profile had enough data." columns={[
          { key: "key", label: "Profile" },
          { key: "t", label: "Trades", numeric: true, render: (x: Any) => num(x.trades, 0) },
          { key: "w", label: "Win rate", numeric: true, render: (x: Any) => pct(x.win_rate, 1) },
          { key: "e", label: "Per trade (R)", numeric: true, render: (x: Any) => num(x.expectancy_r, 2) },
          { key: "n", label: "Net", numeric: true, render: (x: Any) => <span className={x.net_return > 0 ? "text-up" : "text-down"}>{pct(x.net_return, 2)}</span> },
          { key: "d", label: "Worst drop", numeric: true, render: (x: Any) => pct(x.max_drawdown, 1) },
          { key: "why", label: "Failed checks", render: (x: Any) => <span className="text-xs text-muted">{failed(x.gates)}</span> },
        ]} />
      )}
      <p className="mt-2 text-xs text-muted">Research only: it places no orders. Rules written before the run: {n.preregistration}</p>
    </Card>
  );
}

function NightScalper({ n }: { n: Any }) {
  if (!n?.accounts) return null;
  const rows = Object.entries(n.accounts).map(([k, x]: [string, Any]) => ({ key: k, ...x.metrics, passed: x.passed, gates: x.gates }));
  const anyPassed = rows.some((x: Any) => x.passed);
  const d = n.diagnostic_without_halts;
  return (
    <Card title="Forex night scalper (the idea behind popular MT5 robots)" actions={<Badge tone={anyPassed ? "good" : "bad"}>{anyPassed ? "passed" : "none passed"}</Badge>}>
      <Table rows={rows} empty="No result." columns={[
        { key: "key", label: "Exness account" },
        { key: "t", label: "Trades", numeric: true, render: (x: Any) => num(x.trades, 0) },
        { key: "w", label: "Win rate", numeric: true, render: (x: Any) => pct(x.win_rate, 1) },
        { key: "e", label: "Per trade (R)", numeric: true, render: (x: Any) => num(x.expectancy_r, 2) },
        { key: "n", label: "Net", numeric: true, render: (x: Any) => <span className={x.net_return > 0 ? "text-up" : "text-down"}>{pct(x.net_return, 2)}</span> },
        { key: "d", label: "Worst drop", numeric: true, render: (x: Any) => pct(x.max_drawdown, 1) },
        { key: "why", label: "Failed checks", render: (x: Any) => <span className="text-xs text-muted">{failed(x.gates)}</span> },
      ]} />
      <p className="mt-2 text-xs text-muted">Commercial &quot;night scalper&quot; robots fade small moves in the quiet hours after the New York close. Their code is closed, so NEXUS tested the public idea: 12 pairs, one trade at a time with a stop, real Exness bid and ask prices tick by tick (Standard account 2015–2026, Raw Spread 2020–2026 with its commission), NEXUS risk limits. Both accounts hit the 5% drawdown stop.{d ? ` With the stop switched off, every single year lost money (Standard ${num(d.Standard?.expectancy_r, 2)} R a trade, Raw Spread ${num(d["Raw Spread"]?.expectancy_r, 2)} R).` : ""} Historical backtest, no orders placed. Rules written before the run: {n.preregistration}</p>
    </Card>
  );
}

function BinanceGrid({ g }: { g: Any }) {
  if (!g?.configs) return null;
  const h = g.holding;
  const rows = [...Object.entries(g.configs).map(([k, x]: [string, Any]) => ({ key: k, ...x.metrics, passed: x.passed, gates: x.gates })),
    { key: "Holding the same coins (benchmark)", ...h, benchmark: true }];
  const anyPassed = Object.values(g.configs).some((x: Any) => x.passed);
  return (
    <Card title="Binance Spot Grid bot, as people run it (10 coins, 2021 – Oct 2026)" actions={<Badge tone={anyPassed ? "good" : "bad"}>{anyPassed ? "passed" : "none passed"}</Badge>}>
      <Table rows={rows} empty="No result." columns={[
        { key: "key", label: "Grid setting" },
        { key: "n", label: "Net", numeric: true, render: (x: Any) => <span className={x.net_return > 0 ? "text-up" : "text-down"}>{pct(x.net_return, 1)}</span> },
        { key: "s", label: "Sharpe", numeric: true, render: (x: Any) => num(x.sharpe, 2) },
        { key: "d", label: "Worst drop", numeric: true, render: (x: Any) => pct(x.max_drawdown, 1) },
        { key: "why", label: "Failed checks", render: (x: Any) => x.benchmark ? DASH : <span className="text-xs text-muted">{failed(x.gates)}</span> },
      ]} />
      <p className="mt-2 text-xs text-muted">The plain Binance grid: a fixed price range around the current price, equal coin amount per grid, a new bot every 30 days, 0.1% fees, on Binance&apos;s own 5-minute prices. Narrow grids traded hundreds of thousands of times and lost most of the money; the widest grid (±30%, no stop) made +{num(100 * (g.configs["±30% / 30 grids"]?.metrics?.net_return ?? 0), 0)}% in 6 years, but holding the same coins made far more for the risk taken, and almost all of the grid&apos;s profit came in 2021. Grids cap the gains in rallies and keep the full losses in crashes. Historical backtest, no orders placed. Rules written before the run: {g.preregistration}</p>
    </Card>
  );
}

function MultiAsset() {
  const { data: r } = useApi<Any>("/research/multi-asset", 60_000);
  if (!r?.trend && !r?.ndx_voltarget) return null;
  const t = r.trend;
  const v = r.ndx_voltarget;
  const trows = Object.entries(t?.strategies ?? {}).map(([k, x]: [string, Any]) => ({ key: k, ...x }));
  const vrows = v ? [{ key: "Volatility-managed (15% target)", ...v.target_15 }, { key: "Held at 100%", ...v.hold }] : [];
  const f = r.fx_styles;
  const FX_NAMES: Record<string, string> = { carry: "Carry (buy high-interest, sell low-interest)", dollar_carry: "Dollar carry", momentum: "Momentum (3 months)", value: "Value (5-year reversal)", combined: "Combined (Deutsche Bank style)" };
  const frows = Object.entries(f?.strategies ?? {}).map(([k, x]: [string, Any]) => ({ key: k, ...x }));
  return (
    <>
      {t && (
        <Card title="Forex majors and NASDAQ-100: trend following (2004 – Oct 2026)" actions={<Badge tone={t.summary?.passed?.length ? "good" : "bad"}>{t.summary?.passed?.length ? "passed" : "none passed"}</Badge>}>
          <Table rows={trows} empty="No result." columns={[
            { key: "key", label: "Strategy", render: (x: Any) => TREND_NAMES[x.key] ?? x.key },
            { key: "a", label: "Per year", numeric: true, render: (x: Any) => pct(x.annual_return, 2) },
            { key: "s", label: "Sharpe", numeric: true, render: (x: Any) => num(x.sharpe, 2) },
            { key: "d", label: "Worst drop", numeric: true, render: (x: Any) => pct(x.max_drawdown, 1) },
            { key: "h1", label: "2004–2015", numeric: true, render: (x: Any) => pct(x.halves?.["2004_2015"]?.net_return, 1) },
            { key: "h2", label: "2016–2026", numeric: true, render: (x: Any) => pct(x.halves?.["2016_2026"]?.net_return, 1) },
            { key: "why", label: "Failed checks", render: (x: Any) => <span className="text-xs text-muted">{failed(x.gates)}</span> },
          ]} />
          <p className="mt-2 text-xs text-muted">Seven currencies against the dollar (with their interest-rate carry) and the NASDAQ-100, official Federal Reserve (FRED) data, never more exposure than the money in the account. Trend following earned a little before 2016 and lost it afterwards. Holding the NASDAQ-100 over the same years: {pct(t.ndx_price_hold?.annual_return, 1)} a year (worst drop {pct(t.ndx_price_hold?.max_drawdown, 0)}).</p>
        </Card>
      )}
      {f && (
        <Card title="Forex: the institutional styles on the 9 G10 currencies (2004 – Oct 2026)" actions={<Badge tone={f.summary?.passed?.length ? "good" : "bad"}>{f.summary?.passed?.length ? "passed" : "none passed"}</Badge>}>
          <Table rows={frows} empty="No result." columns={[
            { key: "key", label: "Strategy", render: (x: Any) => FX_NAMES[x.key] ?? x.key },
            { key: "a", label: "Per year", numeric: true, render: (x: Any) => <span className={x.annual_return > 0 ? "text-up" : "text-down"}>{pct(x.annual_return, 2)}</span> },
            { key: "s", label: "Sharpe", numeric: true, render: (x: Any) => num(x.sharpe, 2) },
            { key: "d", label: "Worst drop", numeric: true, render: (x: Any) => pct(x.max_drawdown, 1) },
            { key: "p", label: "p-value", numeric: true, render: (x: Any) => num(x.p_value, 2) },
            { key: "why", label: "Failed checks", render: (x: Any) => <span className="text-xs text-muted">{failed(x.gates)}</span> },
          ]} />
          <p className="mt-2 text-xs text-muted">The systematic styles banks and funds publish (carry, momentum, value; Deutsche Bank's Currency Returns index combines them), tested with interest-rate carry, real costs and no more exposure than the money in the account. Carry came closest: about 1.1% a year, positive in 16 of 23 years, with a 2008-style crash. At a retail broker such as Exness, carry depends on the broker's swap rates, which the Exness bridge reports. Banks' own forex profits come mostly from market making and internal matching of client orders, which a retail account cannot copy.</p>
        </Card>
      )}
      <Nqs1 n={r.nqs1} />
      <NightScalper n={r.night_scalper} />
      <BinanceGrid g={r.binance_grid} />
      {v && (
        <Card title="NASDAQ-100: hold less when prices swing wildly (1990 – Oct 2026)" actions={<Badge tone={v.passed ? "good" : "bad"}>{v.passed ? "passed" : "failed"}</Badge>}>
          <Table rows={vrows} empty="No result." columns={[
            { key: "key", label: "" },
            { key: "a", label: "Per year", numeric: true, render: (x: Any) => pct(x.annual_return, 1) },
            { key: "s", label: "Sharpe", numeric: true, render: (x: Any) => num(x.sharpe, 2) },
            { key: "d", label: "Worst drop", numeric: true, render: (x: Any) => pct(x.max_drawdown, 0) },
          ]} />
          <p className="mt-2 text-xs text-muted">The same rule that passed for BTC/ETH: it earned less than holding but its worst drop was about half as deep. Paper forward test from 27 Nov 2026 on the All markets page. Rules written before the run: {r.preregistrations?.join(", ")}</p>
        </Card>
      )}
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
          <Batch3 />
          <MarketMaking />
          <StatArb />
          <MultiAsset />
          <p className="text-xs text-muted">Generated {time(r.generated_at)} by {r.engine} from {r.source}. A strategy passes only if it made money, had a profit factor of at least 1.2, never fell more than 25%, made at least 50 trades, was positive in 2 of 3 years, stayed profitable at double fees, and its edge was statistically significant after correcting for testing many strategies at once.</p>
        </div>
      )}
    </>
  );
}
