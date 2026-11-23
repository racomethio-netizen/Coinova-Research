"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { Badge, Button, Card, Field, Loading, PageHeader, Stat, Table } from "@/components/ui";
import { DASH, num, pct, time } from "@/lib/format";
import { useApi } from "@/lib/hooks";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

const CHECKS: [string, string][] = [
  ["trend_5m", "5m close > EMA20 > EMA50"], ["confirm_15m", "15m EMA20 > EMA50, MACD up"],
  ["filter_1h", "1h close > EMA50"], ["rsi", "RSI 52-70, rising"], ["macd", "MACD above signal, rising"],
  ["volume", "Volume > 1.2x average"], ["beats_costs", "Target covers 3x costs"],
];

function OwnerApproval({ r, reload }: { r: Any; reload: () => Promise<void> | void }) {
  const [ack, setAck] = useState("");
  const [totp, setTotp] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const appr = r.strategy?.owner_approval;
  const send = async (active: boolean) => {
    setMsg(null);
    try {
      await api.post(`/strategies/${r.strategy.code}/owner-approval`, active ? { active, acknowledgement: ack, totp } : { active });
      setAck(""); setTotp(""); await reload();
    } catch (e) { setMsg(e instanceof Error ? e.message : String(e)); }
  };
  return (
    <Card title="Owner approval for the main trader" actions={<Badge tone={appr?.active ? "warn" : "muted"}>{appr?.active ? "approved" : "not approved"}</Badge>}>
      <p className="mb-2 text-sm">Approval lets the main trader (the engine that can trade live, behind every live gate) run this strategy although it has not passed validation. In paper mode it trades the simulated main portfolio. Real orders additionally need live mode, your live approval, a verified key with trading permission and a funded ledger.</p>
      {appr?.active ? (
        <div className="flex flex-wrap items-center gap-3 text-sm"><span>Approved by {appr.actor} at {time(appr.at)}.</span><Button tone="danger" onClick={() => send(false)}>Withdraw approval</Button></div>
      ) : (
        <div className="grid gap-3 md:grid-cols-3">
          <Field id="bot-ack" label={`Type: ${r.strategy?.acknowledgement ?? ""}`} value={ack} onChange={setAck} />
          <Field id="bot-totp" label="Two-factor code" value={totp} onChange={setTotp} inputMode="numeric" />
          <div className="flex items-end"><Button tone="primary" disabled={ack !== r.strategy?.acknowledgement || totp.length !== 6} onClick={() => send(true)}>Approve</Button></div>
        </div>
      )}
      {msg && <p role="alert" className="mt-2 text-sm text-down">{msg}</p>}
    </Card>
  );
}

function EdgeMonitor() {
  const { data, reload } = useApi<Any>("/strategies/edge-monitor", 30_000);
  const [reason, setReason] = useState("");
  const [totp, setTotp] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const rows: Any[] = data?.monitors ?? [];
  const reset = async (m: Any) => {
    setMsg(null);
    try { await api.post(`/strategies/${m.strategy}/edge-reset`, { portfolio_id: m.portfolio_id, reason, totp }); setReason(""); setTotp(""); await reload(); }
    catch (e) { setMsg(e instanceof Error ? e.message : String(e)); }
  };
  const tone = (st: string) => (st === "NORMAL" ? "good" : st === "REDUCE" ? "warn" : "bad");
  return (
    <Card title="Edge monitor (stops a strategy whose edge disappears)">
      <p className="mb-2 text-sm text-muted">After every closed trade it checks the strategy&apos;s recent results in R. A sustained drop below breakeven (CUSUM) or a 15R drawdown stops new entries; a negative recent window or a 10R drawdown halves the size. It only tightens by itself; relaxing needs your two-factor code.</p>
      {rows.length === 0 ? <p className="text-sm text-muted">No closed trades evaluated yet.</p> : (
        <div className="space-y-3">
          {rows.map((m: Any) => (
            <div key={`${m.strategy}-${m.portfolio_id}`} className="rounded border border-line p-3 text-sm">
              <div className="mb-1 flex flex-wrap items-center justify-between gap-2"><span>{m.strategy} · {m.portfolio ?? m.portfolio_id}</span><Badge tone={tone(m.state)}>{m.state}</Badge></div>
              <p className="text-xs text-muted">{m.trades ?? 0} trades · CUSUM {m.cusum ?? DASH} · recent mean {m.rolling_mean ?? DASH}R · drawdown {m.drawdown_r ?? DASH}R</p>
              {(m.reasons ?? []).length > 0 && <ul className="mt-1 list-disc pl-5 text-xs">{m.reasons.map((x: string) => <li key={x}>{x}</li>)}</ul>}
              {m.state !== "NORMAL" && (
                <div className="mt-2 grid gap-2 md:grid-cols-3">
                  <Field id={`edge-reason-${m.portfolio_id}`} label="Reason for the reset" value={reason} onChange={setReason} />
                  <Field id={`edge-totp-${m.portfolio_id}`} label="Two-factor code" value={totp} onChange={setTotp} inputMode="numeric" />
                  <div className="flex items-end"><Button disabled={reason.trim().length < 5 || totp.length !== 6} onClick={() => reset(m)}>Reset to NORMAL</Button></div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
      {msg && <p role="alert" className="mt-2 text-sm text-down">{msg}</p>}
    </Card>
  );
}

export default function TradingBot() {
  const { data: r, error, loading, reload } = useApi<Any>("/trading-bot", 15_000);
  const st = r?.status;
  const risk = st?.risk ?? {};
  const open = (r?.positions ?? []).filter((p: Any) => p.status === "open");
  const closed = (r?.positions ?? []).filter((p: Any) => p.status === "closed");
  return (
    <>
      <PageHeader title="Trading bot" subtitle="One clear strategy (EMA 20/50 trend, RSI, MACD, volume, 15m and 1h filters, ATR stop) running every 5 minutes on Binance spot prices. Paper mode fills orders against the live Binance order book with a simulated balance." />
      <div role="status" className="mb-4 rounded border border-down/40 bg-down/10 px-3 py-2 text-sm font-semibold text-down">LIVE TRADING DISABLED · mode: {r?.mode ?? DASH} · simulated balance, no real orders</div>
      <Loading error={error} loading={loading && !r} />
      {r && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label="Simulated equity" value={risk.equity == null ? DASH : `${num(risk.equity)} USDT`} hint={`started with ${num(r.portfolio?.initial_capital)} virtual USDT`} />
            <Stat label="Closed trades" value={String(r.performance?.closed_trades ?? 0)} hint={r.performance?.win_rate == null ? "no closed trades yet" : `win rate ${pct(r.performance.win_rate, 0)}`} />
            <Stat label="Net P&L (simulated)" value={r.performance ? `${num(r.performance.net_pnl)} USDT` : DASH} tone={(r.performance?.net_pnl ?? 0) > 0 ? "up" : (r.performance?.net_pnl ?? 0) < 0 ? "down" : "flat"} hint={`fees ${num(r.performance?.fees ?? 0)}`} />
            <Stat label="Last scan" value={st?.at ? time(st.at).slice(11, 16) : DASH} hint={st?.kill_switch ? "kill switch engaged" : "every 5 minutes"} tone={st?.kill_switch ? "down" : "flat"} />
          </div>
          <Card title="Live evaluation (latest closed 5m bar)">
            {st ? (
              <div className="grid gap-3 md:grid-cols-2">
                {Object.entries(st.symbols ?? {}).map(([sym, info]: [string, Any]) => (
                  <div key={sym} className="rounded border border-line p-3 text-sm">
                    <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                      <span className="font-medium">{sym}</span>
                      <Badge tone={info.last_eval?.entry ? "good" : "muted"}>{info.last_eval?.entry ? "entry signal" : "no trade"}</Badge>
                    </div>
                    {info.note && <p className="mb-2 text-xs text-warn">{info.note}</p>}
                    {!info.fresh && <p className="mb-2 text-xs text-warn">Market data is stale: no entries.</p>}
                    <ul className="space-y-1">
                      {CHECKS.map(([k, label]) => {
                        const v = info.last_eval?.checks?.[k];
                        return <li key={k} className="flex justify-between gap-2"><span>{label}</span><Badge tone={v ? "good" : "bad"}>{v === undefined ? DASH : v ? "yes" : "no"}</Badge></li>;
                      })}
                    </ul>
                    <p className="mt-2 text-xs text-muted">Bar {info.last_eval?.bar ? time(info.last_eval.bar) : DASH} · {info.bars} bars stored</p>
                  </div>
                ))}
              </div>
            ) : <p className="text-sm text-muted">Waiting for the first scan.</p>}
          </Card>
          <Card title="Open positions (simulated)">
            <Table rows={open} empty="No open positions." columns={[
              { key: "symbol", label: "Pair" },
              { key: "opened_at", label: "Opened", render: (p: Any) => time(p.opened_at) },
              { key: "quantity", label: "Size", numeric: true, render: (p: Any) => num(p.quantity, 6) },
              { key: "entry", label: "Entry", numeric: true, render: (p: Any) => num(p.entry) },
              { key: "stop", label: "Stop-loss", numeric: true, render: (p: Any) => num(p.stop) },
              { key: "target", label: "Take-profit", numeric: true, render: (p: Any) => num(p.target) },
            ]} />
          </Card>
          <Card title="Trade history (simulated)">
            <Table rows={closed} empty="No closed trades yet." columns={[
              { key: "symbol", label: "Pair" },
              { key: "opened_at", label: "Opened", render: (p: Any) => time(p.opened_at) },
              { key: "closed_at", label: "Closed", render: (p: Any) => time(p.closed_at) },
              { key: "exit_reason", label: "Exit", render: (p: Any) => p.exit_reason ?? DASH },
              { key: "realized_pnl", label: "P&L", numeric: true, render: (p: Any) => `${num(p.realized_pnl)} USDT` },
            ]} />
          </Card>
          <Card title="Recorded signals">
            <Table rows={r.signals} empty="No qualifying or near-qualifying signals yet." columns={[
              { key: "bar_time", label: "Bar", render: (x: Any) => time(x.bar_time) },
              { key: "symbol", label: "Pair" },
              { key: "status", label: "Result", render: (x: Any) => <Badge tone={x.status === "executed" ? "good" : "muted"}>{x.status}</Badge> },
              { key: "reason", label: "Why", render: (x: Any) => x.reason ?? "all checks passed" },
            ]} />
          </Card>
          <Card title="Orders (paper)">
            <Table rows={r.orders} empty="No orders yet." columns={[
              { key: "time", label: "Time", render: (o: Any) => time(o.time) },
              { key: "symbol", label: "Pair" },
              { key: "side", label: "Side" },
              { key: "purpose", label: "Purpose", render: (o: Any) => o.reason ?? o.purpose },
              { key: "status", label: "Status" },
              { key: "filled", label: "Filled", numeric: true, render: (o: Any) => `${num(o.filled, 6)} / ${num(o.amount, 6)}` },
              { key: "fee", label: "Fee", numeric: true, render: (o: Any) => num(o.fee, 4) },
            ]} />
          </Card>
          <EdgeMonitor />
          <OwnerApproval r={r} reload={reload} />
          <Card title="Risk limits">
            <p className="text-sm">Risk {pct(r.risk.risk_per_trade, 2)} per trade (max {pct(r.risk.max_risk_per_trade, 2)}) · daily loss {pct(r.risk.max_daily_drawdown, 0)} · drawdown {pct(r.risk.max_drawdown, 0)} · up to {r.risk.max_positions} positions · stop 1.5 ATR · take-profit 2R · time stop 4 hours.</p>
            <p className="mt-2 text-xs text-muted">This type of indicator strategy lost money after costs in every NEXUS backtest. It runs so the full pipeline can be watched working; no profit is implied.</p>
          </Card>
        </div>
      )}
    </>
  );
}
