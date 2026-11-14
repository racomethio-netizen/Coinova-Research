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
  return (
    <>
      <PageHeader title="Risk management" subtitle="Limits can only be tightened. Breaches block new entries; the max-drawdown breach engages the kill switch." />
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
          </Card>
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
