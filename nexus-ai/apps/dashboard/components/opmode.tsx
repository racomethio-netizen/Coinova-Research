"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { Badge, Button, Card, Field } from "@/components/ui";
import { time } from "@/lib/format";
import { useApi } from "@/lib/hooks";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

const LABEL: Record<string, string> = {
  ANALYSIS_ONLY: "Analysis only",
  SIGNALS_ONLY: "Signals only",
  PAPER_TRADING: "Paper trading",
  LIVE_AUTONOMOUS: "Live autonomous",
};
const MEANING: Record<string, string> = {
  ANALYSIS_ONLY: "Market analysis only. No signals are recorded and no orders are placed.",
  SIGNALS_ONLY: "Signals are generated and risk-checked and shown with the verdict. Nothing is executed.",
  PAPER_TRADING: "Signals that pass every check become paper orders on real order books. No real money.",
  LIVE_AUTONOMOUS: "Signals that pass every check become REAL orders with real money.",
};

/** Owner choice of operating mode. Open positions stay protected in every mode. */
export function OperatingModeCard() {
  const { data, reload } = useApi<Any>("/settings/operating-mode", 15000);
  const [mode, setMode] = useState("");
  const [reason, setReason] = useState("");
  const [confirm, setConfirm] = useState("");
  const [totp, setTotp] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  if (!data) return null;
  const live = mode === "LIVE_AUTONOMOUS";
  const submit = async () => {
    setMsg(null);
    try {
      await api.post("/settings/operating-mode", { mode, reason, confirm: confirm || null, totp: totp || null });
      setMode(""); setReason(""); setConfirm(""); setTotp(""); await reload();
    } catch (e) { setMsg(e instanceof Error ? e.message : String(e)); }
  };
  return (
    <Card title="Operating mode" actions={<Badge tone={data.mode === "LIVE_AUTONOMOUS" ? "bad" : data.mode === "PAPER_TRADING" ? "info" : "muted"}>{LABEL[data.mode]}</Badge>}>
      <p className="text-sm">{MEANING[data.mode]}</p>
      <p className="mt-1 text-xs text-muted">{data.set_by ? `Set by ${data.set_by} at ${time(data.at)}: ${data.reason}` : "Default (never changed)."} Open positions keep their stops and targets in every mode.</p>
      <div className="mt-3 grid gap-3 md:grid-cols-4">
        <div>
          <label htmlFor="opmode" className="mb-1 block text-sm">New mode</label>
          <select id="opmode" value={mode} onChange={(e) => setMode(e.target.value)} className="w-full rounded-lg border border-line bg-elevated px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-accent">
            <option value="">Choose…</option>
            {data.modes.map((m: string) => <option key={m} value={m}>{LABEL[m]}</option>)}
          </select>
        </div>
        <Field id="opmode-reason" label="Reason for the change" value={reason} onChange={setReason} />
        {live && <Field id="opmode-confirm" label={`Type: ${data.live_confirm_phrase}`} value={confirm} onChange={setConfirm} autoComplete="off" />}
        {live && <Field id="opmode-totp" label="Two-factor code" value={totp} onChange={setTotp} inputMode="numeric" />}
        <div className="flex items-end"><Button tone={live ? "danger" : "default"} disabled={!mode || reason.trim().length < 5} onClick={submit}>Change mode</Button></div>
      </div>
      {msg && <p role="alert" className="mt-2 text-sm text-down">{msg}</p>}
      {data.live_blockers.length > 0 && (
        <div className="mt-3 border-t border-line pt-3 text-sm">
          <p className="mb-1 font-medium">Live autonomous is locked until all of these are fixed:</p>
          <ul className="list-disc pl-5 text-muted">{data.live_blockers.map((b: string) => <li key={b}>{b}</li>)}</ul>
        </div>
      )}
    </Card>
  );
}

/** Owner-confirmed starting balance for the live ledger, bounded by Binance's report. */
export function FundLiveLedgerCard() {
  const [amount, setAmount] = useState("");
  const [confirm, setConfirm] = useState("");
  const [totp, setTotp] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const valid = /^\d+(\.\d+)?$/.test(amount) && Number(amount) > 0;
  const submit = async () => {
    setMsg(null);
    try {
      const r = await api.post<Any>("/live/ledger/fund", { amount, asset: "USDT", confirm, totp: totp || null });
      setMsg(`Live ledger now manages ${r.ledger_total} USDT.`); setAmount(""); setConfirm(""); setTotp("");
    } catch (e) { setMsg(e instanceof Error ? e.message : String(e)); }
  };
  return (
    <Card title="Live ledger starting balance">
      <p className="text-sm">The live trader starts with an empty ledger and cannot buy anything. Here you allow it to manage part of the USDT that Binance reports as free. It can never be more than Binance reports, and nothing moves on Binance.</p>
      <div className="mt-3 grid gap-3 md:grid-cols-4">
        <Field id="fund-amount" label="USDT amount" value={amount} onChange={setAmount} inputMode="numeric" error={amount && !valid ? "Enter a positive number" : null} />
        <Field id="fund-confirm" label="Type: FUND LIVE LEDGER" value={confirm} onChange={setConfirm} autoComplete="off" />
        <Field id="fund-totp" label="Two-factor code" value={totp} onChange={setTotp} inputMode="numeric" />
        <div className="flex items-end"><Button tone="danger" disabled={!valid || confirm !== "FUND LIVE LEDGER"} onClick={submit}>Confirm starting balance</Button></div>
      </div>
      {msg && <p role="status" className="mt-2 text-sm">{msg}</p>}
    </Card>
  );
}
