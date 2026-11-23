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
  const [ack, setAck] = useState("");
  const [approveTotp, setApproveTotp] = useState("");
  const [busy, setBusy] = useState(false);
  const run = async (fn: () => Promise<unknown>, ok: string) => {
    if (busy) return;                       // one request per tap: a 2FA code is single-use
    setBusy(true);
    setMsg(null);
    try { await fn(); setMsg(ok); await sec.reload(); await me.reload(); }
    catch (e) { setMsg(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); setApproveTotp(""); }
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
        <Card title="Exchange API keys">
          <p className="text-sm">Binance keys are added, tested and disconnected on the <a className="underline" href="/connection/">Binance connection</a> page.</p>
        </Card>
        <Card title="Live trading approval" actions={<Badge tone={sec.data?.live_trading_permitted ? "bad" : "good"}>{sec.data?.live_trading_permitted ? "LIVE PERMITTED" : "live blocked"}</Badge>}>
          <p className="mb-2 text-sm">Live trading needs every gate below cleared. Currently blocking:</p>
          <ul className="mb-3 list-disc pl-5 text-sm text-warn">{sec.data?.live_trading_blockers?.map((b: string) => <li key={b}>{b}</li>)}</ul>
          <div className="grid gap-3 md:grid-cols-2">
            <Field id="ack" label={`Type exactly: ${PHRASE}`} value={ack} onChange={setAck} />
            <Field id="approve-totp" label="Two-factor code (6 digits, enter a fresh one just before tapping)" value={approveTotp} onChange={setApproveTotp} inputMode="numeric" />
          </div>
          <div className="mt-3 flex gap-2">
            <Button tone="danger" disabled={busy || ack !== PHRASE || !/^\d{6}$/.test(approveTotp.trim())} onClick={() => run(() => api.post("/security/live-approval", { approve: true, acknowledgement: ack, totp: approveTotp.trim() }), "Approval recorded. Configuration gates still apply.")}>Approve live trading</Button>
            <Button disabled={busy} onClick={() => run(() => api.post("/security/live-approval", { approve: false, acknowledgement: "", totp: approveTotp.trim() }), "Approval revoked.")}>Revoke approval</Button>
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
