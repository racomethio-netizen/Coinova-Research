"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { Badge, Button, Card, Field } from "@/components/ui";
import { DASH, time } from "@/lib/format";
import { useApi } from "@/lib/hooks";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

const TONE: Record<string, "good" | "warn" | "bad" | "muted"> = {
  NORMAL: "good", REDUCED_RISK: "warn", NO_NEW_TRADES: "warn", CLOSE_ONLY: "bad", EMERGENCY_STOP: "bad",
};
const MEANING: Record<string, string> = {
  NORMAL: "New trades allowed at the configured risk.",
  REDUCED_RISK: "New trades allowed at reduced size.",
  NO_NEW_TRADES: "No new trades. Open positions keep their stops and targets.",
  CLOSE_ONLY: "No new trades and resting entry orders are cancelled.",
  EMERGENCY_STOP: "Kill switch: everything stopped.",
};
const FLOORS = ["NORMAL", "REDUCED_RISK", "NO_NEW_TRADES", "CLOSE_ONLY"];

/** Risk state per mode (derived by the trader every cycle) + owner minimum state. */
export function RiskStateCard({ mode }: { mode: "paper" | "live" }) {
  const { data, reload } = useApi<Any>("/risk/state", 10000);
  const [floor, setFloor] = useState("");
  const [reason, setReason] = useState("");
  const [totp, setTotp] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const m = data?.[mode];
  const cur = m?.current;
  const recon = m?.reconciliation;
  const submit = async () => {
    setMsg(null);
    try {
      await api.post("/risk/state/floor", { mode, state: floor, reason, totp: totp || null });
      setReason(""); setTotp(""); setFloor(""); await reload();
    } catch (e) { setMsg(e instanceof Error ? e.message : String(e)); }
  };
  return (
    <Card title={`Risk state (${mode})`} actions={<Badge tone={cur ? TONE[cur.state] : "muted"}>{cur ? cur.state : "not reported"}</Badge>}>
      {!cur && <p className="text-sm text-muted">The {mode} trader has not reported a state yet (it is not running in this mode, or has not completed a cycle).</p>}
      {cur && (
        <div className="space-y-1 text-sm">
          <p>{MEANING[cur.state]}{cur.state === "REDUCED_RISK" ? ` Risk multiplier ${cur.risk_multiplier}.` : ""}</p>
          {cur.reasons.length > 0
            ? <ul className="list-disc pl-5 text-muted">{cur.reasons.map((r: string) => <li key={r}>{r}</li>)}</ul>
            : <p className="text-muted">No restricting condition.</p>}
          <p className="text-xs text-muted">Updated {time(cur.at)}{m.last_transition ? ` · last change ${m.last_transition.from ?? "start"} to ${m.last_transition.to} at ${time(m.last_transition.at)}` : ""}</p>
        </div>
      )}
      <div className="mt-3 border-t border-line pt-3 text-sm">
        <p className="mb-1 font-medium">Exchange balance check</p>
        {!recon && <p className="text-muted">{DASH}</p>}
        {recon && recon.status === "not_applicable" && <p className="text-muted">Not applicable in paper mode: the paper ledger is the record.</p>}
        {recon && recon.status !== "not_applicable" && (
          <>
            <p><Badge tone={recon.status === "ok" ? "good" : "bad"}>{recon.status}</Badge> checked {time(recon.checked_at)}{recon.detail ? `: ${recon.detail}` : ""}</p>
            {recon.mismatches.length > 0 && (
              <ul className="mt-1 list-disc pl-5 text-xs text-down">{recon.mismatches.map((x: Any) => <li key={`${x.exchange}-${x.asset}`}>{x.exchange} {x.asset}: NEXUS ledger {x.internal}, exchange {x.exchange_total} (short {x.shortfall})</li>)}</ul>
            )}
          </>
        )}
      </div>
      {m && (
        <div className="mt-3 border-t border-line pt-3">
          <p className="mb-2 text-sm">Your minimum state: <strong>{m.owner_floor}</strong>. It can only add restrictions; limits the trader detects still apply.{mode === "live" ? " Relaxing it needs your two-factor code." : ""}</p>
          <div className="grid gap-3 md:grid-cols-4">
            <div>
              <label htmlFor={`floor-${mode}`} className="mb-1 block text-sm">Minimum state</label>
              <select id={`floor-${mode}`} value={floor} onChange={(e) => setFloor(e.target.value)} className="w-full rounded-lg border border-line bg-elevated px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-accent">
                <option value="">Choose…</option>
                {FLOORS.map((f) => <option key={f} value={f}>{f}</option>)}
              </select>
            </div>
            <Field id={`floor-reason-${mode}`} label="Reason for this minimum state" value={reason} onChange={setReason} />
            {mode === "live" && <Field id={`floor-totp-${mode}`} label="Two-factor code (to relax)" value={totp} onChange={setTotp} inputMode="numeric" />}
            <div className="flex items-end"><Button disabled={!floor || reason.trim().length < 5} onClick={submit}>Set minimum state</Button></div>
          </div>
          {msg && <p role="alert" className="mt-2 text-sm text-down">{msg}</p>}
        </div>
      )}
    </Card>
  );
}
