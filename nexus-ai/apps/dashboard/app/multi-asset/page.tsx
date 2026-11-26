"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { Badge, Button, Card, Field, Loading, PageHeader } from "@/components/ui";
import { time } from "@/lib/format";
import { useApi } from "@/lib/hooks";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

const MEANING: Record<string, string> = {
  OFF: "No new signals and no new orders. Open positions stay protected.",
  RESEARCH: "Data and analysis; signals are recorded with their risk verdict. No orders.",
  PAPER: "Signals that pass every check become paper orders. No real money.",
  LIVE: "Signals that pass every check become REAL orders with real money.",
};
const TONE: Record<string, "bad" | "info" | "muted"> = { LIVE: "bad", PAPER: "info", RESEARCH: "muted", OFF: "muted" };

function MarketCard({ m, reload }: { m: Any; reload: () => Promise<void> }) {
  const [mode, setMode] = useState("");
  const [reason, setReason] = useState("");
  const [confirm, setConfirm] = useState("");
  const [totp, setTotp] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const live = mode === "LIVE";
  const id = m.market;
  const submit = async () => {
    setMsg(null);
    try {
      await api.post(`/markets/${id}/autonomy`, { autonomy: mode, reason, confirm: confirm || null, totp: totp || null });
      setMode(""); setReason(""); setConfirm(""); setTotp(""); await reload();
    } catch (e) { setMsg(e instanceof Error ? e.message : String(e)); }
  };
  return (
    <Card title={m.name} actions={<Badge tone={TONE[m.autonomy]}>{m.autonomy}</Badge>}>
      <p className="text-sm">{MEANING[m.autonomy]}</p>
      <p className="mt-1 text-xs text-muted">
        {m.set_by ? `Set by ${m.set_by} at ${time(m.at)}: ${m.reason}` : `Default (${m.default}), never changed.`} Effective with the operating mode: {m.effective_mode} (paper orders: {m.paper_orders ? "yes" : "no"}, live orders: {m.live_orders ? "yes" : "no"}).
      </p>
      {m.paper_positions && <p className="mt-1 text-sm">Paper positions: {m.paper_positions.open} open, {m.paper_positions.closed} closed. Details on the Paper trading and Open positions pages.</p>}
      <div className="mt-3 space-y-2">
        {m.venues.map((v: Any) => (
          <div key={v.code} className="rounded-lg border border-line p-3 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{v.name}</span>
              <Badge tone={v.connector === "built" ? "good" : "muted"}>{v.connector === "built" ? "connector built" : "not built yet"}</Badge>
            </div>
            <p className="mt-1 text-xs text-muted">{v.instruments.join("; ")}. Access: {v.access}.</p>
            {v.notes && <p className="mt-1 text-xs text-muted">{v.notes}</p>}
            {v.owner_needs.length > 0 && <p className="mt-1 text-xs">Needed from you: {v.owner_needs.join("; ")}.</p>}
          </div>
        ))}
      </div>
      {m.notes.length > 0 && <ul className="mt-2 list-disc pl-5 text-xs text-muted">{m.notes.map((n: string) => <li key={n}>{n}</li>)}</ul>}
      {m.allowed.length > 1 ? (
        <div className="mt-3 grid gap-3 md:grid-cols-4">
          <div>
            <label htmlFor={`mode-${id}`} className="mb-1 block text-sm">New setting</label>
            <select id={`mode-${id}`} value={mode} onChange={(e) => setMode(e.target.value)} className="w-full rounded-lg border border-line bg-elevated px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-accent">
              <option value="">Choose…</option>
              {m.allowed.map((a: string) => <option key={a} value={a}>{a}</option>)}
            </select>
          </div>
          <Field id={`reason-${id}`} label="Reason for the change" value={reason} onChange={setReason} />
          {live && <Field id={`confirm-${id}`} label={`Type: ${m.live_confirm_phrase}`} value={confirm} onChange={setConfirm} autoComplete="off" />}
          {live && <Field id={`totp-${id}`} label="Two-factor code" value={totp} onChange={setTotp} inputMode="numeric" />}
          <div className="flex items-end"><Button tone={live ? "danger" : "default"} disabled={!mode || reason.trim().length < 5} onClick={submit}>Change</Button></div>
        </div>
      ) : (
        <p className="mt-3 text-sm">Stays OFF until NEXUS has a data source, a paper engine and a broker connector for this market.</p>
      )}
      {msg && <p role="alert" className="mt-2 text-sm text-down">{msg}</p>}
      {m.live_blockers?.length > 0 && (
        <div className="mt-3 border-t border-line pt-3 text-sm">
          <p className="mb-1 font-medium">LIVE is locked until all of these are fixed:</p>
          <ul className="list-disc pl-5 text-muted">{m.live_blockers.map((b: string) => <li key={b}>{b}</li>)}</ul>
        </div>
      )}
    </Card>
  );
}

export default function MultiAssetPage() {
  const { data, error, loading, reload } = useApi<Any>("/markets", 15000);
  return (
    <>
      <PageHeader title="All markets" subtitle="Crypto, stocks, commodities and forex: one switch per market, each with its own live authorization" />
      <Loading error={error} loading={loading && !data} />
      {data && (
        <div className="space-y-4">
          <p className="rounded-lg border border-line bg-elevated p-3 text-sm">
            Global operating mode: <strong>{data.operating_mode}</strong>; server trading mode: <strong>{data.server_trading_mode}</strong>. A market switch can only narrow the operating mode, never widen it. Live trading is never switched on by code: each market needs its own typed phrase, your two-factor code and its live checks.
          </p>
          {data.markets.map((m: Any) => <MarketCard key={m.market} m={m} reload={reload} />)}
        </div>
      )}
    </>
  );
}
