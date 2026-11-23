"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { Badge, Button, Card, Field, Loading, PageHeader } from "@/components/ui";
import { DASH, time } from "@/lib/format";
import { useApi } from "@/lib/hooks";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

const EMPTY = { label: "main", api_key: "", api_secret: "", totp: "" };

function CheckResult({ check }: { check: Any }) {
  if (!check) return <p className="text-sm text-muted">Not tested yet.</p>;
  const markets = ["futures", "spot"].filter((m) => check[m]);
  return (
    <div className="space-y-2 text-sm">
      <p className="text-xs text-muted">Last check {time(check.checked_at)} ({check.environment})</p>
      {markets.map((m) => {
        const r = check[m];
        return (
          <div key={m} className="rounded-lg border border-line p-3">
            <div className="mb-1 flex items-center gap-2">
              <span className="font-medium capitalize">{m}</span>
              <Badge tone={r.ok ? "good" : "bad"}>{r.ok ? "connected" : "failed"}</Badge>
            </div>
            {r.ok ? (
              <ul className="space-y-0.5 text-xs text-muted">
                {r.wallet_balance_usdt !== undefined && <li>Wallet balance: <span className="num">{r.wallet_balance_usdt}</span> USDT</li>}
                {r.available_balance_usdt !== undefined && <li>Available: <span className="num">{r.available_balance_usdt}</span> USDT</li>}
                {r.position_mode && <li>Position mode: {r.position_mode}{r.note ? ` (${r.note})` : ""}</li>}
                {r.usdt_total !== undefined && <li>USDT: <span className="num">{r.usdt_total}</span>, assets with balance: {r.assets_with_balance}</li>}
              </ul>
            ) : (
              <p role="alert" className="text-xs text-down">{r.error}</p>
            )}
          </div>
        );
      })}
    </div>
  );
}

function TestnetRoundTrip() {
  const { data, reload } = useApi<Any>("/system/testnet/round-trip", 0);
  const [totp, setTotp] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const run = async () => {
    setBusy(true); setMsg(null);
    try { await api.post("/system/testnet/round-trip", { totp }); setTotp(""); await reload(); }
    catch (e) { setMsg(e instanceof Error ? e.message : String(e)); }
    setBusy(false);
  };
  const last = data?.runs?.[0];
  const s = last?.steps ?? {};
  return (
    <Card title="Binance Spot TESTNET round trip (test funds only)" actions={last ? <Badge tone={last.result === "ROUND_TRIP_FILLED" ? "good" : "warn"}>{last.result}</Badge> : <Badge>not run</Badge>}>
      <p className="mb-2 text-sm">Runs on the server with your stored TESTNET key: connection, balances, real testnet candles and order book, the trend signal, risk sizing (0.25%), a buy and a sell confirmed from Binance by client order ID, and a restart drill that must find the order instead of placing it twice. Never uses a production key.</p>
      <div className="grid gap-2 md:grid-cols-3">
        <Field id="testnet-totp" label="Two-factor code" value={totp} onChange={setTotp} inputMode="numeric" />
        <div className="flex items-end"><Button tone="primary" disabled={busy || totp.length !== 6} onClick={run}>{busy ? "Running…" : "Run testnet round trip"}</Button></div>
      </div>
      {msg && <p role="alert" className="mt-2 text-sm text-down">{msg}</p>}
      {last && (
        <ul className="mt-3 space-y-1 text-sm">
          <li>Run {last.run_id} at {time(last.at)}{last.error ? ` · ${last.error}` : ""}</li>
          <li>Connection: {s.connectivity?.ok ? `ok, ${s.connectivity.latency_ms} ms` : DASH}</li>
          <li>Signal: {s.signal?.decision ?? DASH}{s.signal?.adx !== undefined ? ` (ADX ${s.signal.adx.toFixed(1)}, RSI ${s.signal.rsi.toFixed(1)})` : ""}</li>
          <li>Risk: {s.risk ? (s.risk.approved ? `approved ${s.risk.quantity} (risk ${s.risk.risk_amount} USDT)` : `rejected: ${s.risk.reason}`) : DASH}</li>
          <li>Buy: {s.buy ? `${s.buy.status}, filled ${s.buy.filled} at ${s.buy.average_price ?? DASH}, order ${s.buy.exchange_order_id}` : DASH}</li>
          <li>Sell: {s.sell ? `${s.sell.status}, filled ${s.sell.filled} at ${s.sell.average_price ?? DASH}` : DASH}</li>
          <li>Restart drill: {s.restart_drill ? (s.restart_drill.resubmitted ? "FAILED: resubmitted" : "passed: found on Binance, not resubmitted") : DASH}</li>
        </ul>
      )}
    </Card>
  );
}

export default function ConnectionPage() {
  const me = useApi<Any>("/auth/me");
  const creds = useApi<{ credentials: Any[] }>("/security/credentials");
  const [form, setForm] = useState(EMPTY);
  const [environment, setEnvironment] = useState<"testnet" | "production">("production");
  const [markets, setMarkets] = useState<string[]>(["futures"]);
  const [noWithdraw, setNoWithdraw] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const run = async (key: string, fn: () => Promise<unknown>, ok: string) => {
    setMsg(null); setBusy(key);
    try { await fn(); setMsg(ok); await creds.reload(); }
    catch (e) { setMsg(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(null); }
  };
  const toggleMarket = (m: string) =>
    setMarkets((cur) => (cur.includes(m) ? cur.filter((x) => x !== m) : [...cur, m]));
  const list = creds.data?.credentials ?? [];

  return (
    <>
      <PageHeader title="Binance connection" subtitle="API keys are encrypted on the server and never sent back to the browser. Testing a key makes read-only calls and places no orders." />
      <Loading error={creds.error} loading={creds.loading && !creds.data} />
      {msg && <p role="status" className="mb-3 rounded-lg border border-line bg-elevated p-3 text-sm">{msg}</p>}
      <div className="space-y-4">
        <Card title="Connected keys">
          {list.length === 0 && <p className="text-sm text-muted">No Binance key connected.</p>}
          <div className="space-y-3">
            {list.map((c: Any) => (
              <div key={c.id} className="rounded-xl border border-line p-4">
                <div className="mb-2 flex flex-wrap items-center gap-2">
                  <span className="font-medium">{c.label}</span>
                  <Badge tone={c.environment === "production" ? "warn" : "info"}>{c.environment}</Badge>
                  {c.markets.map((m: string) => <Badge key={m}>{m}</Badge>)}
                  <span className="num text-xs text-muted">key …{c.fingerprint}</span>
                </div>
                <p className="mb-2 text-xs text-muted">Added {time(c.created_at)} · last verified {c.last_verified_at ? time(c.last_verified_at) : DASH}</p>
                <CheckResult check={c.last_check} />
                <div className="mt-3 flex flex-wrap gap-2">
                  <Button tone="primary" disabled={busy !== null} onClick={() => run(`test-${c.id}`, () => api.post(`/security/credentials/${c.id}/test`), "Connection test finished.")}>
                    {busy === `test-${c.id}` ? "Testing…" : "Test connection"}
                  </Button>
                  <Button tone="danger" disabled={busy !== null} onClick={() => {
                    if (window.confirm(`Disconnect "${c.label}"? The stored key is revoked and can no longer be used.`)) {
                      void run(`del-${c.id}`, () => api.del(`/security/credentials/${c.id}`), "Key disconnected.");
                    }
                  }}>Disconnect</Button>
                </div>
              </div>
            ))}
          </div>
        </Card>

        <Card title="Connect a Binance API key">
          {!me.data?.totp_enabled && (
            <p role="alert" className="mb-3 rounded-lg border border-warn/40 bg-warn/10 p-3 text-sm text-warn">
              Enable two-factor authentication on the Security page first. Adding a key requires a two-factor code.
            </p>
          )}
          <ul className="mb-3 list-disc space-y-1 pl-5 text-xs text-muted">
            <li>Production keys: enable Reading plus only the trading permissions you choose. Withdrawals must be DISABLED, and restrict the key to your server&apos;s IP.</li>
            <li>Binance refuses some server locations (HTTP 451). The test result says so if that happens. NEXUS does not work around it.</li>
          </ul>
          <fieldset className="mb-3">
            <legend className="mb-1 text-xs text-muted">Environment</legend>
            <div className="flex gap-4 text-sm">
              {(["production", "testnet"] as const).map((e) => (
                <label key={e} className="flex items-center gap-2">
                  <input type="radio" name="env" checked={environment === e} onChange={() => setEnvironment(e)} /> {e}
                </label>
              ))}
            </div>
          </fieldset>
          <fieldset className="mb-3">
            <legend className="mb-1 text-xs text-muted">Markets this key may use</legend>
            <div className="flex gap-4 text-sm">
              {["futures", "spot"].map((m) => (
                <label key={m} className="flex items-center gap-2">
                  <input type="checkbox" checked={markets.includes(m)} onChange={() => toggleMarket(m)} /> {m}
                </label>
              ))}
            </div>
          </fieldset>
          <div className="grid gap-3 md:grid-cols-2">
            <Field id="conn-label" label="Label" value={form.label} onChange={(v) => setForm({ ...form, label: v })} />
            <Field id="conn-totp" label="Two-factor code" value={form.totp} onChange={(v) => setForm({ ...form, totp: v })} inputMode="numeric" />
            <Field id="conn-key" label="API key" value={form.api_key} onChange={(v) => setForm({ ...form, api_key: v })} autoComplete="off" />
            <Field id="conn-secret" label="API secret" type="password" value={form.api_secret} onChange={(v) => setForm({ ...form, api_secret: v })} autoComplete="off" />
          </div>
          <label className="mt-3 flex items-center gap-2 text-sm">
            <input type="checkbox" checked={noWithdraw} onChange={(e) => setNoWithdraw(e.target.checked)} /> I confirm withdrawals are disabled for this key
          </label>
          <div className="mt-3">
            <Button tone="primary" disabled={busy !== null || markets.length === 0 || !noWithdraw}
              onClick={() => run("add", async () => {
                await api.post("/security/credentials", { exchange: "binance", ...form, environment, markets, withdrawals_disabled_confirmed: noWithdraw });
                setForm(EMPTY); setNoWithdraw(false);
              }, "Key stored (encrypted). Run a connection test next.")}>
              {busy === "add" ? "Saving…" : "Save key"}
            </Button>
          </div>
        </Card>
        <TestnetRoundTrip />
      </div>
    </>
  );
}
