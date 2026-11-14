"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { Badge, Button, Card, Field, Loading, PageHeader, Table } from "@/components/ui";
import { time } from "@/lib/format";
import { useApi } from "@/lib/hooks";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;
const PHRASE = "I accept that live trading can lose real money";

export default function SecurityPage() {
  const me = useApi<Any>("/auth/me");
  const sec = useApi<Any>("/security");
  const [setup, setSetup] = useState<{ secret: string; uri: string } | null>(null);
  const [code, setCode] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [cred, setCred] = useState({ exchange: "binance", label: "main", api_key: "", api_secret: "", totp: "" });
  const [noWithdraw, setNoWithdraw] = useState(false);
  const [ack, setAck] = useState("");
  const [approveTotp, setApproveTotp] = useState("");
  const run = async (fn: () => Promise<unknown>, ok: string) => {
    setMsg(null);
    try { await fn(); setMsg(ok); await sec.reload(); await me.reload(); }
    catch (e) { setMsg(e instanceof Error ? e.message : String(e)); }
  };
  return (
    <>
      <PageHeader title="Security settings" subtitle="Two-factor authentication, exchange API keys, live-trading approval and the audit log" />
      <Loading error={sec.error} loading={sec.loading && !sec.data} />
      {msg && <p role="status" className="mb-3 rounded-lg border border-line bg-elevated p-3 text-sm">{msg}</p>}
      <div className="space-y-4">
        <Card title="Two-factor authentication" actions={<Badge tone={me.data?.totp_enabled ? "good" : "warn"}>{me.data?.totp_enabled ? "enabled" : "not enabled"}</Badge>}>
          {!me.data?.totp_enabled && !setup && <Button tone="primary" onClick={() => run(async () => setSetup(await api.post("/auth/totp/setup")), "Scan the secret in your authenticator app, then confirm.")}>Set up 2FA</Button>}
          {setup && (
            <div className="space-y-3 text-sm">
              <p>Secret: <code className="num break-all">{setup.secret}</code></p>
              <Field id="totp-confirm" label="Code from your app" value={code} onChange={setCode} inputMode="numeric" />
              <Button tone="primary" onClick={() => run(async () => { await api.post("/auth/totp/enable", { code }); setSetup(null); }, "Two-factor enabled.")}>Confirm</Button>
            </div>
          )}
        </Card>
        <Card title="Add exchange API key">
          <p className="mb-3 text-xs text-muted">Create keys with READ + SPOT TRADE permissions only. Withdrawals and transfers must be DISABLED, and restrict the key to your server IP. Keys are encrypted (AES-256-GCM) and never shown again.</p>
          <div className="grid gap-3 md:grid-cols-2">
            <div>
              <label htmlFor="cred-ex" className="mb-1 block text-xs text-muted">Exchange</label>
              <select id="cred-ex" className="w-full rounded-lg border border-line bg-elevated px-3 py-2 text-sm" value={cred.exchange} onChange={(e) => setCred({ ...cred, exchange: e.target.value })}>
                {["binance", "kraken", "coinbase"].map((x) => <option key={x}>{x}</option>)}
              </select>
            </div>
            <Field id="cred-label" label="Label" value={cred.label} onChange={(v) => setCred({ ...cred, label: v })} />
            <Field id="cred-key" label="API key" value={cred.api_key} onChange={(v) => setCred({ ...cred, api_key: v })} autoComplete="off" />
            <Field id="cred-secret" label="API secret" type="password" value={cred.api_secret} onChange={(v) => setCred({ ...cred, api_secret: v })} autoComplete="off" />
            <Field id="cred-totp" label="Two-factor code" value={cred.totp} onChange={(v) => setCred({ ...cred, totp: v })} inputMode="numeric" />
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={noWithdraw} onChange={(e) => setNoWithdraw(e.target.checked)} /> I confirm withdrawals are disabled for this key</label>
          </div>
          <div className="mt-3"><Button tone="primary" onClick={() => run(() => api.post("/security/credentials", { ...cred, withdrawals_disabled_confirmed: noWithdraw }), "Key stored (encrypted).")}>Store key</Button></div>
        </Card>
        <Card title="Live trading approval" actions={<Badge tone={sec.data?.live_trading_permitted ? "bad" : "good"}>{sec.data?.live_trading_permitted ? "LIVE PERMITTED" : "live blocked"}</Badge>}>
          <p className="mb-2 text-sm">Live trading needs every gate below cleared. Currently blocking:</p>
          <ul className="mb-3 list-disc pl-5 text-sm text-warn">{sec.data?.live_trading_blockers?.map((b: string) => <li key={b}>{b}</li>)}</ul>
          <div className="grid gap-3 md:grid-cols-2">
            <Field id="ack" label={`Type exactly: ${PHRASE}`} value={ack} onChange={setAck} />
            <Field id="approve-totp" label="Two-factor code" value={approveTotp} onChange={setApproveTotp} inputMode="numeric" />
          </div>
          <div className="mt-3 flex gap-2">
            <Button tone="danger" disabled={ack !== PHRASE} onClick={() => run(() => api.post("/security/live-approval", { approve: true, acknowledgement: ack, totp: approveTotp }), "Approval recorded. Configuration gates still apply.")}>Approve live trading</Button>
            <Button onClick={() => run(() => api.post("/security/live-approval", { approve: false, acknowledgement: "", totp: approveTotp }), "Approval revoked.")}>Revoke approval</Button>
          </div>
        </Card>
        <Card title="Audit log (append-only)">
          <Table rows={sec.data?.audit} empty="No audit entries." columns={[
            { key: "created_at", label: "Time", render: (a: Any) => time(a.created_at) },
            { key: "actor", label: "Actor" }, { key: "action", label: "Action" },
            { key: "target", label: "Target", render: (a: Any) => a.target_type ? `${a.target_type} ${a.target_id ?? ""}` : "—" },
            { key: "ip_address", label: "IP" },
          ]} />
        </Card>
      </div>
    </>
  );
}
