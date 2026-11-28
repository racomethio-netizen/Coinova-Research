"use client";

import Link from "next/link";
import { useState } from "react";
import { api } from "@/lib/api";
import { Badge, Button, Card, Field, Loading, PageHeader, Table } from "@/components/ui";
import { DASH, time } from "@/lib/format";
import { useApi } from "@/lib/hooks";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

const TONE: Record<string, "good" | "warn" | "bad" | "muted"> = { ok: "good", warn: "warn", critical: "bad" };
const LABEL: Record<string, string> = { ok: "OK", warn: "WARNING", critical: "CRITICAL" };
const DOMAINS: [string, string, string][] = [
  ["platform", "Platform health", "Database, Redis, memory, disk, every worker, heartbeats"],
  ["market_data", "Market data", "Candles fresh and complete, prices consistent, live quotes, forex feed"],
  ["models", "Models", "ML models in use and the forward record of each switched-on strategy module"],
  ["execution", "Execution", "Paper/demo orders answered, stop-losses verified, rejections, slippage"],
  ["risk", "Risk", "Kill switch, risk state, losses against the limits, drawdown, open risk, reconciliation"],
];

function Status({ s }: { s: string }) {
  return <Badge tone={TONE[s] ?? "muted"}>{LABEL[s] ?? s}</Badge>;
}

export default function GuardianPage() {
  const { data, error, loading, reload } = useApi<Any>("/guardian", 15_000);
  const [reason, setReason] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const rep = data?.report;
  const stop = async () => {
    setMsg(null);
    try { await api.post("/risk/kill-switch", { reason }); setReason(""); await reload(); }
    catch (e) { setMsg(e instanceof Error ? e.message : String(e)); }
  };
  return (
    <>
      <PageHeader title="Guardian" subtitle="24/7 watchdog: platform, market data, models, execution and risk, checked every minute" />
      <Loading error={error} loading={loading && !data} />
      {data && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <span>Overall:</span>{rep ? <Status s={rep.overall} /> : <Badge tone="muted">no report</Badge>}
            <span className={data.stale ? "text-warn" : "text-muted"}>
              {rep ? `last check ${Math.round(data.age_seconds)}s ago${data.stale ? " (stale: the Guardian is not running)" : ""}` : "the Guardian has not run yet"}
            </span>
            <span className="text-muted">Alerts: {data.alerts_channel}</span>
          </div>
          <Card title="Emergency stop" actions={<Badge tone={data.kill_switch ? "bad" : "good"}>{data.kill_switch ? "KILL SWITCH ENGAGED" : "kill switch clear"}</Badge>}>
            {data.kill_switch ? (
              <p className="text-sm">Engaged by {data.kill_switch.actor} at {time(data.kill_switch.at)}: {data.kill_switch.reason}. New entries are blocked everywhere; open positions keep their stops. Only you can clear it, on the <Link className="underline" href="/risk/">Risk center</Link> page with your two-factor code.</p>
            ) : (
              <div className="grid gap-3 md:grid-cols-3">
                <Field id="guardian-reason" label="Reason (required)" value={reason} onChange={setReason} />
                <div className="flex items-end">
                  <Button tone="danger" disabled={reason.trim().length < 5} onClick={stop}>Engage kill switch</Button>
                </div>
              </div>
            )}
            {msg && <p role="alert" className="mt-2 text-sm text-down">{msg}</p>}
            <p className="mt-3 text-xs text-muted">{data.note}</p>
          </Card>
          {rep && (rep.actions ?? []).length > 0 && (
            <Card title="Guardian actions this run">
              <ul className="list-disc pl-5 text-sm">{rep.actions.map((a: string) => <li key={a}>{a}</li>)}</ul>
            </Card>
          )}
          {rep && DOMAINS.map(([key, title, hint]) => {
            const d = rep.domains?.[key] ?? {};
            const rows = (rep.checks ?? []).filter((c: Any) => c.domain === key)
              .sort((a: Any, b: Any) => (b.status === "ok" ? 0 : b.status === "warn" ? 1 : 2) - (a.status === "ok" ? 0 : a.status === "warn" ? 1 : 2));
            return (
              <Card key={key} title={title} actions={<span className="flex items-center gap-2 text-xs text-muted">{d.problems ?? 0} of {d.checks ?? 0} need attention <Status s={d.status ?? "ok"} /></span>}>
                <p className="mb-2 text-xs text-muted">{hint}</p>
                <Table rows={rows} empty="No checks in this area." columns={[
                  { key: "status", label: "Status", render: (c: Any) => <Status s={c.status} /> },
                  { key: "name", label: "Check" },
                  { key: "detail", label: "Detail", render: (c: Any) => <span className="text-sm">{c.detail}{c.shutdown ? " (emergency-shutdown condition)" : ""}</span> },
                  { key: "since", label: "Since", render: (c: Any) => c.since ? time(c.since) : DASH },
                ]} />
              </Card>
            );
          })}
          <Card title="Recent alerts">
            <Table rows={data.alerts ?? []} empty="No alerts yet." columns={[
              { key: "created_at", label: "Time", render: (x: Any) => time(x.created_at) },
              { key: "severity", label: "Severity", render: (x: Any) => <Badge tone={x.severity === "critical" ? "bad" : x.severity === "warning" ? "warn" : "good"}>{x.severity === "info" ? "recovered" : x.severity}</Badge> },
              { key: "message", label: "Alert" },
            ]} />
          </Card>
        </div>
      )}
    </>
  );
}
