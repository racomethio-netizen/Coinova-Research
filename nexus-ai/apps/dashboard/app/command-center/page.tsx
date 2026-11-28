"use client";

import Link from "next/link";
import { EquityChart } from "@/components/charts";
import { Badge, Card, Loading, PageHeader, Stat, Table } from "@/components/ui";
import { DASH, num, pct, signed, time } from "@/lib/format";
import { useApi } from "@/lib/hooks";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

const STATUS_TONE: Record<string, "good" | "warn" | "bad" | "muted"> = { ok: "good", warn: "warn", critical: "bad" };
const STATUS_TEXT: Record<string, string> = { ok: "OK", warn: "WARNING", critical: "CRITICAL" };
const ACTION_TONE: Record<string, "good" | "bad" | "muted"> = { BUY: "good", SELL: "bad", NO_TRADE: "muted" };

function Details({ href }: { href: string }) {
  return <Link className="text-xs text-accent underline" href={href}>Details</Link>;
}

function St({ s }: { s: string | null | undefined }) {
  if (!s) return <Badge tone="muted">no data</Badge>;
  return <Badge tone={STATUS_TONE[s] ?? "muted"}>{STATUS_TEXT[s] ?? s}</Badge>;
}

function r(v: number | null | undefined, d = 2) {
  return v === null || v === undefined ? DASH : `${v >= 0 ? "+" : ""}${v.toFixed(d)}R`;
}

function Meter({ row }: { row: Any }) {
  const used = Math.max(0, Math.min(1, row.used ?? 0));
  const bar = row.status === "critical" ? "bg-down" : row.status === "warn" ? "bg-warn" : "bg-up";
  const v = row.format === "count" ? num(row.value, 0) : row.format === "x" ? `${num(row.value, 2)}x` : pct(row.value);
  const l = row.format === "count" ? num(row.limit, 0) : row.format === "x" ? `${num(row.limit, 2)}x` : pct(row.limit, 1);
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between gap-2 text-sm">
        <span>{row.name}</span>
        <span className="flex items-center gap-2"><span className="tabular-nums">{v} of {l}</span><St s={row.status} /></span>
      </div>
      <div className="h-2 w-full rounded bg-elevated" role="meter" aria-label={row.name} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(used * 100)}>
        <div className={`h-2 rounded ${bar}`} style={{ width: `${used * 100}%` }} />
      </div>
    </div>
  );
}

function Performance({ p, title, href }: { p: Any; title: string; href: string }) {
  const fx = p.label === "DEMO";
  return (
    <Card title={title} actions={<span className="flex items-center gap-2"><Badge tone="info">{p.label}</Badge><Details href={href} /></span>}>
      {p.note && <p className="mb-3 text-xs text-muted">{p.note}</p>}
      {fx ? (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
          <Stat label="Equity" value={num(p.equity)} hint={p.currency ?? undefined} />
          <Stat label="Open P&L" value={num(p.open_pnl)} tone={signed(p.open_pnl)} />
          <Stat label="Today" value={num(p.day_pnl)} tone={signed(p.day_pnl)} />
          <Stat label="This week" value={num(p.week_pnl)} tone={signed(p.week_pnl)} />
          <Stat label="Drawdown" value={pct(p.drawdown)} hint={`max recorded ${pct(p.max_drawdown_recorded)}`} />
          <Stat label="Open positions" value={p.positions ?? DASH} hint={p.connected ? `synced ${time(p.at)}` : "not connected"} tone={p.connected ? undefined : "warn"} />
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
          <Stat label="Equity" value={num(p.equity)} hint={`started ${num(p.initial_capital)}`} />
          <Stat label="Total P&L" value={num(p.total_pnl)} tone={signed(p.total_pnl)} hint={pct(p.total_return)} />
          <Stat label="Realized / unrealized" value={`${num(p.realized_pnl)} / ${num(p.unrealized_pnl)}`} />
          <Stat label="Closed trades" value={p.closed_trades ?? DASH} hint={`win rate ${pct(p.win_rate, 0)}`} />
          <Stat label="Profit factor" value={num(p.profit_factor)} />
          <Stat label="Max drawdown" value={pct(p.max_drawdown)} hint={`now ${pct(p.current_drawdown)}`} />
        </div>
      )}
      <div className="mt-3">
        {(p.curve ?? []).length > 1
          ? <EquityChart points={p.curve} label={`${title} equity, hourly`} />
          : <p className="py-4 text-center text-sm text-muted">Not enough equity history yet.</p>}
      </div>
    </Card>
  );
}

function Checks({ d, empty }: { d: Any; empty: string }) {
  return (
    <>
      {d?.note && <p className="text-sm text-muted">{d.note}</p>}
      <Table rows={d?.checks ?? []} empty={empty} columns={[
        { key: "status", label: "Status", render: (c: Any) => <St s={c.status} /> },
        { key: "detail", label: "Detail", render: (c: Any) => <span className="text-sm">{c.detail}</span> },
      ]} />
    </>
  );
}

export default function CommandCenter() {
  const { data, error, loading } = useApi<Any>("/command-center", 30_000);
  const st = data?.status;
  return (
    <>
      <PageHeader title="Command centre" subtitle="Forex and crypto on one page: performance, strategies, signals, risk, models and execution. Paper and demo only." />
      <Loading error={error} loading={loading && !data} />
      {data && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border border-line bg-elevated p-3 text-sm">
            <span className="flex items-center gap-2">Guardian <St s={st.guardian} />{st.guardian_stale && <span className="text-warn">(not running)</span>}</span>
            <span className="flex items-center gap-2">Kill switch <Badge tone={st.kill_switch ? "bad" : "good"}>{st.kill_switch ? "ENGAGED" : "clear"}</Badge></span>
            <span className="flex items-center gap-2">Real money <Badge tone="muted">{st.real_money}</Badge></span>
            <span>Mode <strong>{st.operating_mode ?? DASH}</strong></span>
            <span>Switches: crypto <strong>{st.switches?.crypto ?? DASH}</strong>, forex <strong>{st.switches?.forex ?? DASH}</strong>, gold <strong>{st.switches?.commodities ?? DASH}</strong></span>
            <span>Risk state <strong>{st.risk_state ?? DASH}</strong></span>
            <span className="text-muted">updated {time(data.at)}</span>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Performance p={data.forex} title="Forex performance" href="/multi-asset/" />
            <Performance p={data.crypto} title="Crypto performance" href="/paper/" />
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card title="Profit and loss, drawdown" actions={<Details href="/performance/" />}>
              <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
                <Stat label="Combined equity" value={num(data.pnl.combined_equity)} hint="USD, both accounts" />
                <Stat label="Today" value={data.pnl.today_pct == null ? DASH : pct(data.pnl.today_pct)} tone={data.pnl.today_pct == null ? undefined : data.pnl.today_pct < 0 ? "down" : "up"} />
                <Stat label="This week" value={data.pnl.week_pct == null ? DASH : pct(data.pnl.week_pct)} tone={data.pnl.week_pct == null ? undefined : data.pnl.week_pct < 0 ? "down" : "up"} />
                <Stat label="Drawdown (combined)" value={pct(data.drawdown.combined_current)} hint={`pause at ${pct(data.drawdown.pause_at, 0)}`} />
                <Stat label="Crypto max drawdown" value={pct(data.drawdown.crypto_max)} hint={`now ${pct(data.drawdown.crypto_current)}`} />
                <Stat label="Forex max drawdown" value={pct(data.drawdown.forex_max_recorded)} hint={`now ${pct(data.drawdown.forex_current)}`} />
              </div>
              <p className="mt-3 text-xs text-muted">{data.pnl.note}</p>
            </Card>
            <Card title="Risk exposure" actions={<Details href="/portfolio-risk/" />}>
              {data.exposure.note && <p className="text-sm text-muted">{data.exposure.note}</p>}
              <div className="space-y-3">{(data.exposure.rows ?? []).map((row: Any) => <Meter key={row.name} row={row} />)}</div>
              <p className="mt-3 text-xs text-muted">
                {(data.exposure.accounts ?? []).map((a: Any) => `${a.name}: ${num(a.equity)} USD, ${a.reconciled ? "reconciled" : "NOT reconciled (entries blocked)"}`).join(" · ") || "No account in the risk book yet."}
                {data.exposure.at ? ` · as of ${time(data.exposure.at)}` : ""}
              </p>
            </Card>
          </div>

          <Card title="Open positions" actions={<Details href="/portfolio-risk/" />}>
            <Table rows={data.positions} empty="No open positions." columns={[
              { key: "account", label: "Account", render: (p: Any) => p.account === "crypto_paper" ? "crypto paper" : "Exness demo" },
              { key: "symbol", label: "Instrument" },
              { key: "side", label: "Side" },
              { key: "quantity", label: "Quantity", numeric: true },
              { key: "entry", label: "Entry", numeric: true },
              { key: "price", label: "Price", numeric: true, render: (p: Any) => p.price ?? DASH },
              { key: "stop", label: "Stop", numeric: true, render: (p: Any) => p.stop ?? "none" },
              { key: "unrealized_usd", label: "Open P&L (USD)", numeric: true, render: (p: Any) => <span className={signed(p.unrealized_usd) === "down" ? "text-down" : signed(p.unrealized_usd) === "up" ? "text-up" : ""}>{num(p.unrealized_usd)}</span> },
              { key: "risk_usd", label: "At risk (USD)", numeric: true, render: (p: Any) => num(p.risk_usd) },
            ]} />
          </Card>

          <Card title="Signals and market regime" actions={<span className="flex items-center gap-2 text-xs text-muted">{data.signals.counts?.BUY ?? 0} buy · {data.signals.counts?.SELL ?? 0} sell · {data.signals.counts?.NO_TRADE ?? 0} no trade <Details href="/orchestrator/" /></span>}>
            {data.signals.note && <p className="text-sm text-muted">{data.signals.note}</p>}
            {data.signals.stale && data.signals.at && <p className="text-sm text-warn">The board is stale (last {time(data.signals.at)}).</p>}
            <Table rows={data.signals.rows} empty="No instruments on the board." columns={[
              { key: "symbol", label: "Instrument", render: (x: Any) => `${x.symbol} (${x.market})` },
              { key: "regime", label: "Regime", render: (x: Any) => x.regime ?? DASH },
              { key: "action", label: "Decision", render: (x: Any) => <Badge tone={ACTION_TONE[x.action] ?? "muted"}>{x.action === "NO_TRADE" ? "NO TRADE" : x.action}</Badge> },
              { key: "module", label: "Strategy", render: (x: Any) => x.module ?? DASH },
              { key: "expected_r", label: "Tested result", numeric: true, render: (x: Any) => r(x.expected_r) },
              { key: "why", label: "Why", render: (x: Any) => <span className="text-xs">{(x.why ?? []).join("; ") || DASH}{x.order ? ` · order: ${x.order}` : ""}</span> },
            ]} />
          </Card>

          <Card title="The six strategies" actions={<Details href="/strategy-modules/" />}>
            <Table rows={data.strategies} empty="No strategy modules." columns={[
              { key: "name", label: "Strategy", render: (m: Any) => <span>{m.name}<span className="block text-xs text-muted">{m.market} · {m.style}</span></span> },
              { key: "activation", label: "Observe switch", render: (m: Any) => <Badge tone={m.activation === "OFF" ? "muted" : "info"}>{m.activation}</Badge> },
              { key: "paper_orders", label: "Paper orders", render: (m: Any) => <span className={`text-xs ${m.paper_orders?.startsWith("yes") ? "text-up" : "text-muted"}`}>{m.paper_orders}</span> },
              { key: "test", label: "Historical test", render: (m: Any) => <Badge tone={m.test.passed ? "good" : m.test.passed === false ? "bad" : "muted"}>{m.test.passed ? "passed" : m.test.passed === false ? "failed" : "not tested"}</Badge> },
              { key: "tested", label: "Tested (after costs)", render: (m: Any) => <span className="text-xs">{m.test.trades != null ? `${m.test.trades} trades · win ${pct(m.test.win_rate, 0)} · ${r(m.test.mean_r)} · PF ${num(m.test.profit_factor)}` : m.test.sharpe != null ? `Sharpe ${num(m.test.sharpe)} · max DD ${pct(m.test.max_drawdown, 0)}` : DASH}</span> },
              { key: "forward", label: "Forward record", render: (m: Any) => <span className="text-xs">{m.forward.closed ? `${m.forward.closed} closed · win ${pct(m.forward.win_rate, 0)} · ${r(m.forward.mean_r)} · total ${r(m.forward.total_r, 1)}` : `${m.forward.signals ?? 0} signals, none closed`}</span> },
              { key: "health", label: "Health", render: (m: Any) => m.health ? <St s={m.health.status} /> : <span className="text-xs text-muted">not switched on</span> },
            ]} />
          </Card>

          <Card title="Signal confidence and calibration" actions={<Details href="/strategy-modules/" />}>
            <p className="mb-3 text-sm">{data.calibration.explanation}</p>
            {(data.calibration.evidence ?? []).length > 0 && (
              <div className="mb-4">
                <h3 className="mb-1 text-sm font-medium">Evidence behind today&apos;s signals</h3>
                <Table rows={data.calibration.evidence} empty="" columns={[
                  { key: "symbol", label: "Signal", render: (e: Any) => `${e.action} ${e.symbol}` },
                  { key: "module", label: "Strategy" },
                  { key: "expected_r", label: "Tested mean", numeric: true, render: (e: Any) => r(e.expected_r) },
                  { key: "test_trades", label: "Tested trades", numeric: true, render: (e: Any) => e.test_trades ?? DASH },
                  { key: "p_value", label: "p-value", numeric: true, render: (e: Any) => e.p_value == null ? DASH : num(e.p_value, 3) },
                  { key: "sharpe", label: "Sharpe", numeric: true, render: (e: Any) => e.sharpe == null ? DASH : num(e.sharpe) },
                ]} />
              </div>
            )}
            <h3 className="mb-1 text-sm font-medium">Tested vs forward win rate</h3>
            <Table rows={data.calibration.modules} empty="No modules." columns={[
              { key: "name", label: "Strategy" },
              { key: "tested_win_rate", label: "Tested", numeric: true, render: (m: Any) => pct(m.tested_win_rate, 0) },
              { key: "forward_win_rate", label: "Forward", numeric: true, render: (m: Any) => pct(m.forward_win_rate, 0) },
              { key: "forward_trades", label: "Forward trades", numeric: true },
              { key: "verdict", label: "Verdict", render: (m: Any) => <span className="text-xs">{m.verdict}</span> },
            ]} />
            <h3 className="mb-1 mt-4 text-sm font-medium">ML probability filters (measured on unseen years {(data.calibration.ml?.[0]?.test_years ?? []).join(", ")})</h3>
            <Table rows={data.calibration.ml} empty="No ML filter result recorded." columns={[
              { key: "name", label: "Filter for" },
              { key: "auc", label: "AUC", numeric: true, render: (m: Any) => num(m.auc, 3) },
              { key: "brier", label: "Brier (base rate)", numeric: true, render: (m: Any) => `${num(m.brier, 4)} (${num(m.brier_base_rate, 4)})` },
              { key: "brier_skill", label: "Brier skill", numeric: true, render: (m: Any) => num(m.brier_skill, 3) },
              { key: "ece", label: "Calibration error", numeric: true, render: (m: Any) => num(m.ece, 3) },
              { key: "used", label: "Used", render: (m: Any) => <Badge tone="muted">{m.passed ? "passed" : "not used: failed its test"}</Badge> },
            ]} />
          </Card>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card title="Model health" actions={<span className="flex items-center gap-2"><St s={data.models.status} /><Details href="/guardian/" /></span>}>
              <Checks d={data.models} empty="No model checks yet." />
            </Card>
            <Card title="Execution status" actions={<span className="flex items-center gap-2"><St s={data.execution.guardian?.status} /><Details href="/execution/" /></span>}>
              <p className="mb-2 text-sm">{data.execution.total ?? 0} orders · {Object.entries(data.execution.by_status ?? {}).map(([k, v]) => `${v} ${k}`).join(", ") || "none yet"} · average slippage {data.execution.avg_slippage_bps == null ? DASH : `${num(data.execution.avg_slippage_bps, 1)} bps`}</p>
              <Table rows={data.execution.recent ?? []} empty="No orders yet." columns={[
                { key: "created_at", label: "Created", render: (x: Any) => time(x.created_at) },
                { key: "symbol", label: "Order", render: (x: Any) => `${x.side} ${x.symbol}` },
                { key: "status", label: "Status", render: (x: Any) => <Badge tone={x.status === "protected" ? "good" : x.status === "rejected" ? "bad" : "muted"}>{x.status}</Badge> },
                { key: "stop", label: "Stop verified", render: (x: Any) => x.kind === "allocation" ? "n/a" : x.stop_verified ? "yes" : "no" },
              ]} />
              <div className="mt-3"><Checks d={data.execution.guardian} empty="No execution checks yet." /></div>
              <p className="mt-2 text-xs text-muted">{data.execution.note}</p>
            </Card>
          </div>
        </div>
      )}
    </>
  );
}
