"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { Badge, Button, Card, Field, Loading, PageHeader, Stat, Table, statusTone } from "@/components/ui";
import { DASH, num, pct, time } from "@/lib/format";
import { useApi, useMode } from "@/lib/hooks";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

export default function Risk() {
  const { mode } = useMode();
  const { data, error, loading, reload } = useApi<Any>(`/risk?mode=${mode}`, 10000);
  const [reason, setReason] = useState("");
  const [totp, setTotp] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const act = async (path: string, body: object) => {
    setMsg(null);
    try { await api.post(path, body); setReason(""); setTotp(""); await reload(); }
    catch (e) { setMsg(e instanceof Error ? e.message : String(e)); }
  };
  const L = data?.limits;
  const ks = useApi<Any>(data?.kill_switch ? "/risk/kill-switch/state" : null, 10000);
  const [confirm, setConfirm] = useState("");
  return (
    <>
      <PageHeader title="Risk management" subtitle="Owner-set limits. Breaches block new entries; the max-drawdown breach engages the kill switch; a losing streak first halves risk, then pauses entries." />
      <Loading error={error} loading={loading && !data} />
      {data && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label="Risk per trade" value={pct(L.risk_per_trade)} />
            <Stat label="Daily loss limit" value={pct(L.daily_loss_limit)} />
            <Stat label="Weekly loss limit" value={pct(L.weekly_loss_limit)} />
            <Stat label="Max drawdown shutdown" value={pct(L.max_drawdown)} />
            <Stat label="Max open positions" value={L.max_open_positions} />
            <Stat label="Max exposure" value={pct(L.max_portfolio_exposure, 0)} />
            <Stat label="Current drawdown" value={pct(data.current?.drawdown)} tone={Number(data.current?.drawdown ?? 0) > 0.03 ? "warn" : undefined} />
            <Stat label="Current exposure" value={pct(data.current?.exposure_pct)} hint={data.current ? `equity ${num(data.current.equity)}` : undefined} />
          </div>
          <Card title="Emergency kill switch" actions={<Badge tone={data.kill_switch ? "bad" : "good"}>{data.kill_switch ? "ENGAGED" : "clear"}</Badge>}>
            {data.kill_switch && <p className="mb-3 text-sm text-down">Engaged by {data.kill_switch.actor} at {time(data.kill_switch.at)}: {data.kill_switch.reason}</p>}
            <div className="grid gap-3 md:grid-cols-3">
              <Field id="reason" label="Reason (required)" value={reason} onChange={setReason} />
              {data.kill_switch && <Field id="ks-totp" label="Two-factor code (to clear)" value={totp} onChange={setTotp} inputMode="numeric" />}
              <div className="flex items-end gap-2">
                {!data.kill_switch
                  ? <Button tone="danger" disabled={reason.trim().length < 5} onClick={() => act("/risk/kill-switch", { reason })}>Engage kill switch</Button>
                  : <Button disabled={reason.trim().length < 5} onClick={() => act("/risk/kill-switch/clear", { reason, totp })}>Clear kill switch</Button>}
              </div>
            </div>
            {msg && <p role="alert" className="mt-2 text-sm text-down">{msg}</p>}
            {data.kill_switch && ks.data && (
              <div className="mt-4 space-y-3 border-t border-line pt-3 text-sm">
                <p className="text-muted">While engaged, the trader blocks new entries and cancels resting entry orders every cycle. Open positions stay protected by their stops unless you close them below.</p>
                {(["paper", "live"] as const).map((m) => {
                  const st = ks.data.modes[m];
                  return (
                    <div key={m} className="rounded-lg border border-line p-3">
                      <p className="mb-1 font-medium">{m === "live" ? "LIVE" : "Paper"}: {st.open_positions.length} open position(s), {st.active_orders.length} active order(s){st.close_all_requested ? " · close-all requested" : ""}</p>
                      {st.open_positions.length > 0 && (
                        <ul className="mb-2 list-disc pl-5 text-xs">{st.open_positions.map((p: Any) => <li key={p.id}>{p.symbol}: {p.quantity} @ {p.entry_price}, stop {p.stop_loss ?? DASH}</li>)}</ul>
                      )}
                      {st.open_positions.length > 0 && !st.close_all_requested && (
                        <div className="grid gap-2 md:grid-cols-3">
                          <Field id={`close-${m}`} label={`Type: ${st.confirm_phrase}`} value={confirm} onChange={setConfirm} autoComplete="off" />
                          {m === "live" && <Field id="close-totp" label="Two-factor code" value={totp} onChange={setTotp} inputMode="numeric" />}
                          <div className="flex items-end">
                            <Button tone="danger" disabled={confirm !== st.confirm_phrase} onClick={async () => { await act("/risk/kill-switch/close-positions", { mode: m, confirm, totp: totp || null }); setConfirm(""); await ks.reload(); }}>Close all {m} positions</Button>
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </Card>
          {data.loss_streak && (
            <Card title="Loss-streak protection" actions={<Badge tone={data.loss_streak.paused ? "bad" : Number(data.loss_streak.risk_multiplier) < 1 ? "warn" : "good"}>{data.loss_streak.paused ? "ENTRIES PAUSED" : Number(data.loss_streak.risk_multiplier) < 1 ? "risk reduced" : "normal"}</Badge>}>
              <p className="mb-3 text-sm">Consecutive losses: <span className="num">{data.loss_streak.consecutive_losses}</span>. Risk is halved after {data.loss_streak.reduce_after} losses in a row and new entries pause after {data.loss_streak.pause_after}. Current risk multiplier: <span className="num">{data.loss_streak.risk_multiplier}</span>{data.loss_streak.reset_at ? ` · last reset ${time(data.loss_streak.reset_at)}` : ""}.</p>
              {data.loss_streak.paused && (
                <div className="space-y-2">
                  <div className="grid gap-3 md:grid-cols-2">
                    <Field id="streak-reason" label="Reason for resuming (min 5 characters)" value={reason} onChange={setReason} />
                    <Field id="streak-totp" label="Two-factor code" value={totp} onChange={setTotp} inputMode="numeric" />
                  </div>
                  <Button disabled={reason.trim().length < 5} onClick={() => act("/risk/streak/reset", { reason, totp })}>Resume after review</Button>
                </div>
              )}
            </Card>
          )}
          <Card title="Risk events">
            <Table rows={data.events} empty="No risk events." columns={[
              { key: "created_at", label: "Time", render: (e: Any) => time(e.created_at) },
              { key: "event_type", label: "Event" },
              { key: "severity", label: "Severity", render: (e: Any) => <Badge tone={statusTone(e.severity)}>{e.severity}</Badge> },
              { key: "details", label: "Details", render: (e: Any) => <span className="num text-xs text-muted">{e.details ? JSON.stringify(e.details) : DASH}</span> },
            ]} />
          </Card>
        </div>
      )}
    </>
  );
}
