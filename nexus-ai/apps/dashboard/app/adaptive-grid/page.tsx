"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { Badge, Button, Card, Field, Loading, PageHeader, Stat, Table } from "@/components/ui";
import { DASH, num, pct, qty, time } from "@/lib/format";
import { useApi } from "@/lib/hooks";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

const LIMIT_FIELDS: [string, string][] = [
  ["max_order_risk", "Max risk per order (fraction of equity)"], ["daily_loss", "Daily loss limit"],
  ["weekly_loss", "Weekly loss limit"], ["max_drawdown", "Drawdown shutdown"],
  ["max_portfolio_exposure", "Max portfolio exposure"], ["max_symbol_exposure", "Max per-asset exposure"],
  ["max_correlated_exposure", "Max BTC-correlated exposure"], ["max_futures_margin_util", "Max futures margin use"],
  ["max_open_orders", "Max open simulated orders"],
];

function stateTone(s?: string): "good" | "warn" | "bad" | "muted" {
  if (s === "NORMAL" || s === "RUNNING") return "good";
  if (s === "NO_NEW_ORDERS" || s === "PAUSED" || s === "SUSPENDED") return "warn";
  if (s === "SHUTDOWN" || s === "STOPPED") return "bad";
  return "muted";
}

function Controls({ reload }: { reload: () => Promise<void> | void }) {
  const [reason, setReason] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const send = async (action: string) => {
    if (busy) return;
    setBusy(true); setMsg(null);
    try { await api.post("/adaptive-grid/control", { action, reason }); setMsg(`${action} queued: applied within ~10 seconds.`); await reload(); }
    catch (e) { setMsg(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };
  const ok = reason.trim().length >= 5 && !busy;
  return (
    <Card title="Controls (owner, audited)">
      <div className="grid gap-3 md:grid-cols-3">
        <Field id="agrid-reason" label="Reason (at least 5 characters)" value={reason} onChange={setReason} />
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button tone="primary" disabled={!ok} onClick={() => send("start")}>Start paper trading</Button>
        <Button disabled={!ok} onClick={() => send("pause")}>Pause grids</Button>
        <Button disabled={!ok} onClick={() => send("resume")}>Resume grids</Button>
        <Button tone="danger" disabled={!ok} onClick={() => send("stop_all")}>Stop all strategies</Button>
        <Button tone="danger" disabled={!ok} onClick={() => send("emergency_shutdown")}>Emergency shutdown</Button>
      </div>
      <p className="mt-2 text-xs text-muted">Pause cancels resting entries and keeps exits. Stop all and emergency shutdown close every simulated position at the book. Shutdown stays latched until Start. None of these can enable live trading.</p>
      {msg && <p role="status" className="mt-2 text-sm">{msg}</p>}
    </Card>
  );
}

function Limits({ current, reload }: { current: Any; reload: () => Promise<void> | void }) {
  const [vals, setVals] = useState<Record<string, string>>({});
  const [reason, setReason] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const save = async () => {
    setMsg(null);
    try {
      const limits = { ...current, ...Object.fromEntries(Object.entries(vals).filter(([, v]) => v.trim() !== "")) };
      await api.post("/adaptive-grid/limits", { limits, reason });
      setVals({}); setReason(""); setMsg("Limits saved."); await reload();
    } catch (e) { setMsg(e instanceof Error ? e.message : String(e)); }
  };
  return (
    <Card title="Risk limits">
      <p className="mb-2 text-sm text-muted">Hard ceilings: 1% per order, 2% daily, 4% weekly, 10% drawdown, 1x leverage. Values above them are refused.</p>
      <div className="grid gap-3 md:grid-cols-3">
        {LIMIT_FIELDS.map(([k, label]) => (
          <Field key={k} id={`lim-${k}`} label={`${label} (now ${current?.[k] ?? DASH})`} value={vals[k] ?? ""} onChange={(v) => setVals({ ...vals, [k]: v })} inputMode="text" />
        ))}
        <Field id="lim-reason" label="Reason" value={reason} onChange={setReason} />
      </div>
      <div className="mt-3"><Button tone="primary" disabled={reason.trim().length < 5} onClick={save}>Save limits</Button></div>
      {msg && <p role="status" className="mt-2 text-sm">{msg}</p>}
    </Card>
  );
}

function Universe() {
  const { data: u } = useApi<Any>("/adaptive-grid/universe", 60_000);
  return (
    <Card title="Dynamic trading universe">
      {["spot", "futures"].map((m) => (
        <div key={m} className="mb-4">
          <h3 className="mb-1 text-sm font-semibold capitalize">{m} <span className="font-normal text-muted">— scanned {time(u?.[m]?.at)}, {u?.[m]?.symbols_seen ?? DASH} symbols seen, {u?.[m]?.rejected_count ?? DASH} rejected in stage one</span></h3>
          <Table rows={u?.[m]?.ranked} empty="No scan yet." columns={[
            { key: "symbol", label: "Symbol" },
            { key: "sel", label: "Selected", render: (r: Any) => <Badge tone={r.selected ? "good" : "muted"}>{r.selected ? "selected" : "no"}</Badge> },
            { key: "score", label: "Score", numeric: true, render: (r: Any) => num(r.scores?.score, 3) },
            { key: "vol", label: "24h volume", numeric: true, render: (r: Any) => num((r.metrics?.quote_volume ?? 0) / 1e6, 0) + "M" },
            { key: "spread", label: "Spread bps", numeric: true, render: (r: Any) => num(r.metrics?.spread_bps, 2) },
            { key: "atr", label: "Hourly ATR", numeric: true, render: (r: Any) => pct(r.metrics?.atr_pct, 2) },
            { key: "adx", label: "ADX", numeric: true, render: (r: Any) => num(r.metrics?.adx, 1) },
            { key: "why", label: "Reasons", render: (r: Any) => <span className="text-xs text-muted">{r.reasons?.join("; ") || DASH}</span> },
          ]} />
        </div>
      ))}
    </Card>
  );
}

export default function AdaptiveGridPage() {
  const { data: r, error, loading, reload } = useApi<Any>("/adaptive-grid", 10_000);
  const st = r?.status;
  const grids: Any[] = st ? [...(st.accounts?.spot?.grids ?? []), ...(st.accounts?.futures?.grids ?? [])] : [];
  const sum = (k: string) => grids.reduce((a, g) => a + Number(g[k] ?? 0), 0);
  const capital = Number(st?.accounts?.spot?.capital ?? 0) + Number(st?.accounts?.futures?.capital ?? 0);
  const used = grids.reduce((a, g) => a + Number(g.allocation ?? 0), 0);
  const [open, setOpen] = useState<string | null>(null);
  return (
    <>
      <div className="sticky top-0 z-10 mb-3 rounded-lg border border-warn bg-warn/15 p-2 text-center text-sm font-semibold text-warn" role="status">PAPER TRADING: simulated orders and balances on live Binance data. Live trading is disabled for this system.</div>
      <PageHeader title="Adaptive grid" subtitle="Autonomous adaptive grids on dynamically selected Binance spot and USD-M futures markets, behind one central risk manager" />
      <Loading error={error} loading={loading && !r} />
      {r?.backtest && (
        <Card title="Backtest before paper trading (pre-registered, out of sample)" actions={<Badge tone={r.backtest.passed ? "good" : "bad"}>{r.backtest.passed ? "passed" : "FAILED"}</Badge>}>
          <p className="mb-2 text-sm">Walk-forward on 12 Binance markets, Jul 2024 – Oct 2026, with real fees and funding: net <b>{pct(r.backtest.oos?.net_return, 1)}</b>, worst drop {pct(r.backtest.oos?.max_drawdown, 1)}, profit factor {num(r.backtest.oos?.profit_factor, 2)}, win rate {pct(r.backtest.oos?.win_rate, 0)} over {r.backtest.oos?.trips} trips. Holding BTC over the same period: {pct(r.backtest.buy_and_hold?.BTC, 1)}.</p>
          <p className="mb-2 text-sm">The grid made money in sideways and weak-bull markets and lost more when the market turned down while it still held coins. It therefore runs here as a <b>paper forward test only</b>; its settings were not re-tuned to the past.</p>
          <Table rows={Object.entries(r.backtest.by_regime ?? {}).map(([k, v]: [string, Any]) => ({ regime: k, ...v }))} empty="No breakdown." columns={[
            { key: "regime", label: "Regime at exit" },
            { key: "trips", label: "Trips", numeric: true },
            { key: "pnl", label: "P&L (USDT)", numeric: true, render: (x: Any) => <span className={x.pnl >= 0 ? "text-up" : "text-down"}>{num(x.pnl, 0)}</span> },
            { key: "stops", label: "Stop exits", numeric: true },
          ]} />
          <p className="mt-2 text-xs text-muted">Details and the rules written before the run: {r.backtest.preregistration}</p>
        </Card>
      )}
      {r && !st && <Card title="Status"><p className="text-sm">The adaptive grid worker has not reported yet. It starts when the server runs this version.</p></Card>}
      {st && (
        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Stat label="Paper equity" value={num(st.equity, 2) + " USDT"} hint={`capital ${num(capital, 0)}`} />
            <Stat label="Net P&L after costs" value={num(st.pnl, 2)} tone={Number(st.pnl) >= 0 ? "up" : "down"} hint={`drawdown ${pct(st.drawdown, 2)} from peak`} />
            <Stat label="Realized / unrealized" value={`${num(sum("realized"), 2)} / ${num(sum("unrealized"), 2)}`} />
            <Stat label="Fees / funding" value={`${num(sum("fees_paid"), 2)} / ${num(sum("funding_paid"), 2)}`} />
            <Stat label="Capital in grids" value={pct(capital ? used / capital : 0, 1)} hint={`${grids.length} active grids`} />
            <Stat label="Risk state" value={<Badge tone={stateTone(st.risk_state?.state)}>{st.risk_state?.state}</Badge>} hint={st.risk_state?.reasons?.join("; ") || "all limits clear"} />
            <Stat label="System" value={<Badge tone={stateTone(st.control?.status)}>{st.control?.status}</Badge>} hint={st.control?.shutdown ? "EMERGENCY SHUTDOWN latched" : `kill switch ${st.kill_switch === null ? "unreadable" : st.kill_switch ? "ENGAGED" : "clear"}`} />
            <Stat label="Market data" value={`spot ${st.data_ok?.spot ? "ok" : "stale"} · futures ${st.data_ok?.futures ? "ok" : "stale"}`} hint={`updated ${time(st.updated_at)}`} />
          </div>
          <Controls reload={reload} />
          <Card title="Strategy decisions and market regime">
            <Table rows={Object.entries(st.decisions ?? {}).map(([k, d]: [string, Any]) => ({ key: k, ...d, ...(st.regimes?.[k] ?? {}) }))} empty="No symbols analysed yet." columns={[
              { key: "key", label: "Market:symbol" },
              { key: "regime", label: "Regime", render: (d: Any) => <Badge tone={d.mode ? "good" : "muted"}>{d.regime}</Badge> },
              { key: "strategy", label: "Strategy", render: (d: Any) => `${d.strategy}${d.mode ? ` (${d.mode})` : ""}` },
              { key: "adx", label: "ADX", numeric: true, render: (d: Any) => num(d.adx, 1) },
              { key: "atr", label: "ATR", numeric: true, render: (d: Any) => pct(d.atr_pct, 2) },
              { key: "rsi", label: "RSI", numeric: true, render: (d: Any) => num(d.rsi, 1) },
              { key: "why", label: "Why", render: (d: Any) => <span className="text-xs text-muted">{d.reasons?.join("; ")}</span> },
            ]} />
          </Card>
          <Card title="Active grids">
            <Table rows={grids} empty="No grid running: no symbol currently has a suitable regime, or the system is paused." columns={[
              { key: "symbol", label: "Grid", render: (g: Any) => <button className="underline" onClick={() => setOpen(open === g.grid_id ? null : g.grid_id)}>{g.market} {g.symbol}</button> },
              { key: "mode", label: "Mode" },
              { key: "status", label: "Status", render: (g: Any) => <Badge tone={stateTone(g.status)}>{g.status}</Badge> },
              { key: "range", label: "Range", render: (g: Any) => `${num(g.lower, 2)} – ${num(g.upper, 2)}` },
              { key: "levels", label: "Levels", numeric: true },
              { key: "open_orders", label: "Orders", numeric: true },
              { key: "net_pnl", label: "Net P&L", numeric: true, render: (g: Any) => num(g.net_pnl, 2) },
              { key: "round_trips", label: "Round trips", numeric: true },
              { key: "pos", label: "Position", render: (g: Any) => `${qty(g.long_qty)} long / ${qty(g.short_qty)} short` },
              { key: "liq", label: "Liquidation", numeric: true, render: (g: Any) => g.liquidation_price ? num(g.liquidation_price, 2) : DASH },
            ]} />
            {grids.filter((g) => g.grid_id === open).map((g) => (
              <div key={g.grid_id} className="mt-3 rounded-lg border border-line p-3 text-sm">
                <p className="mb-2">Allocation {num(g.allocation, 2)} · stop {g.stop_low ? num(g.stop_low, 2) : DASH}{g.stop_high ? ` / ${num(g.stop_high, 2)}` : ""} · worst case {num(g.worst_case_loss, 2)} · reserved {num(g.reserved, 2)} · margin {num(g.position_margin, 2)}</p>
                <p className="mb-2 text-xs text-muted">Levels: {g.levels_list?.map((x: string) => num(x, 2)).join(" · ")}</p>
                <Table rows={g.orders} empty="No simulated orders." columns={[
                  { key: "kind", label: "Kind" }, { key: "side", label: "Side" },
                  { key: "price", label: "Price", numeric: true, render: (o: Any) => num(o.price, 4) },
                  { key: "qty", label: "Amount", numeric: true, render: (o: Any) => qty(o.qty) },
                ]} />
              </div>
            ))}
          </Card>
          <Card title="Completed trades (simulated fills)">
            <Table rows={r.fills} empty="No fills yet." columns={[
              { key: "at", label: "Time", render: (f: Any) => time(f.at) },
              { key: "grid", label: "Grid", render: (f: Any) => `${f.account} ${f.symbol}` },
              { key: "kind", label: "Kind", render: (f: Any) => `${f.kind === "fill" ? "" : f.kind}${f.reason ? f.reason : ""}` },
              { key: "side", label: "Side" },
              { key: "price", label: "Price", numeric: true, render: (f: Any) => num(f.price, 4) },
              { key: "qty", label: "Amount", numeric: true, render: (f: Any) => qty(f.qty) },
              { key: "fee", label: "Fee", numeric: true, render: (f: Any) => num(f.fee, 4) },
              { key: "pnl", label: "P&L", numeric: true, render: (f: Any) => f.pnl ? num(f.pnl, 4) : DASH },
            ]} />
          </Card>
          <Card title="Strategy performance by symbol (from the ledger)">
            <Table rows={r.performance} empty="No completed trades yet." columns={[
              { key: "symbol", label: "Grid", render: (p: Any) => `${p.market} ${p.symbol}` },
              { key: "fills", label: "Fills", numeric: true },
              { key: "round_trip_exits", label: "Exits", numeric: true },
              { key: "wr", label: "Win rate", numeric: true, render: (p: Any) => p.round_trip_exits ? pct(p.wins / p.round_trip_exits, 0) : DASH },
              { key: "realized", label: "Realized", numeric: true, render: (p: Any) => num(p.realized, 2) },
              { key: "fees", label: "Fees", numeric: true, render: (p: Any) => num(p.fees, 2) },
              { key: "funding", label: "Funding", numeric: true, render: (p: Any) => num(p.funding, 2) },
              { key: "net", label: "Net after costs", numeric: true, render: (p: Any) => num(p.net_after_costs, 2) },
            ]} />
          </Card>
          <Universe />
          <Limits current={st.limits} reload={reload} />
          <Card title="Decision and risk log">
            <Table rows={r.decisions} empty="No events yet." columns={[
              { key: "at", label: "Time", render: (e: Any) => time(e.at) },
              { key: "kind", label: "Event" },
              { key: "sym", label: "Symbol", render: (e: Any) => e.symbol ? `${e.account} ${e.symbol}` : e.account },
              { key: "detail", label: "Detail", render: (e: Any) => <span className="text-xs text-muted">{e.reasons?.join?.("; ") ?? e.reason ?? e.strategy ?? e.state ?? e.command?.action ?? e.regime ?? (e.report?.reasons?.join("; ")) ?? ""}</span> },
            ]} />
          </Card>
        </div>
      )}
    </>
  );
}
