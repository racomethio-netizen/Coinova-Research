"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { Badge, Button, Card, Loading, PageHeader, Table, statusTone } from "@/components/ui";
import { DASH, num, pct, qty, time } from "@/lib/format";
import { useApi, useMode } from "@/lib/hooks";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

const FLAG_LABELS: [string, string][] = [
  ["market_order", "Market"], ["limit_order", "Limit"], ["post_only", "Post-only"], ["stop_loss_order", "Stop-loss order"],
  ["take_profit_order", "Take-profit order"], ["reduce_only", "Reduce-only"], ["set_leverage", "Set leverage"],
  ["set_margin_mode", "Isolated/cross"], ["funding_rate", "Funding rate"], ["open_interest", "Open interest"],
];

function flag(v: Any) {
  return v === true ? <Badge tone="good">yes</Badge> : v === "emulated" ? <Badge tone="warn">emulated</Badge> : v === false ? <Badge tone="bad">no</Badge> : <Badge>unknown</Badge>;
}

function CapabilityRegistry() {
  const { data, reload } = useApi<Any>("/exchanges/capabilities", 0);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const refresh = async () => {
    setBusy(true); setMsg(null);
    try { await api.post("/exchanges/capabilities/refresh", {}); await reload(); } catch (e) { setMsg(e instanceof Error ? e.message : String(e)); }
    setBusy(false);
  };
  const rows = Object.values(data?.exchanges ?? {}) as Any[];
  return (
    <Card title="Exchange capability registry (live detection)" actions={<Button disabled={busy} onClick={refresh}>{busy ? "Detecting…" : "Detect now"}</Button>}>
      <p className="mb-2 text-sm text-muted">Read from each exchange&apos;s public API through CCXT (no keys, no orders), from this server&apos;s location. A block (for example HTTP 451 or 403) is shown as reported, never worked around. Last check: {data?.checked_at ? time(data.checked_at) : DASH}.</p>
      {msg && <p role="alert" className="mb-2 text-sm text-down">{msg}</p>}
      {rows.length === 0 ? <p className="text-sm text-muted">No detection yet. Press Detect now.</p> : (
        <div className="space-y-3">
          {rows.map((r: Any) => (
            <div key={r.exchange} className="rounded border border-line p-3 text-sm">
              <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                <span className="font-medium capitalize">{r.exchange}</span>
                <Badge tone={r.ok ? "good" : "bad"}>{r.ok ? "reachable" : "not available"}</Badge>
              </div>
              {!r.ok && <p className="text-xs text-down">{r.error}</p>}
              {r.ok && (
                <>
                  <p>Spot USDT markets: {r.spot.usdt_markets} · USDT perpetuals: {r.usdt_perpetual.markets} · sandbox: {r.sandbox ? "yes" : "no"} · rate limit: {r.rate_limit_ms ?? DASH} ms</p>
                  {r.usdt_perpetual.sample && <p className="text-xs text-muted">BTC perpetual: contract {r.usdt_perpetual.sample.contract_size ?? DASH}, min {r.usdt_perpetual.sample.min_amount ?? DASH}, max leverage {r.usdt_perpetual.sample.max_leverage ?? "not public"}, fees {pct(r.usdt_perpetual.sample.maker ?? 0, 3)} / {pct(r.usdt_perpetual.sample.taker ?? 0, 3)}</p>}
                  {r.spot.sample && <p className="text-xs text-muted">BTC/USDT spot: tick {r.spot.sample.price_precision}, min {r.spot.sample.min_amount}, fees {pct(r.spot.sample.maker ?? 0, 3)} / {pct(r.spot.sample.taker ?? 0, 3)}</p>}
                  <div className="mt-2 flex flex-wrap gap-2">{FLAG_LABELS.map(([k, l]) => <span key={k} className="flex items-center gap-1 text-xs">{l} {flag(r.capabilities[k])}</span>)}</div>
                </>
              )}
              <p className="mt-2 text-xs text-muted">NEXUS: {r.nexus?.note ?? DASH}</p>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

export default function Exchanges() {
  const { data, error, loading } = useApi<Any[]>("/exchanges", 30000);
  return (
    <>
      <PageHeader title="Exchanges" subtitle="Connectivity, verified capabilities, synced markets and API keys" />
      <Loading error={error} loading={loading && !data} />
      <div className="space-y-4">
        <CapabilityRegistry />
        {data?.map((ex) => (
          <Card key={ex.code} title={ex.name} actions={ex.health ? <Badge tone={ex.health.ok ? "good" : "bad"}>{ex.health.ok ? `up · ${ex.health.latency_ms ?? DASH} ms` : `down · ${ex.health.detail ?? ""}`}</Badge> : <Badge>no health data</Badge>}>
            {ex.capabilities && (
              <dl className="mb-3 grid grid-cols-1 gap-2 text-sm md:grid-cols-3">
                <div><dt className="text-xs text-muted">Order lookup by client ID</dt><dd>{ex.capabilities.client_order_lookup}</dd></div>
                <div><dt className="text-xs text-muted">Sandbox</dt><dd>{ex.capabilities.sandbox_available ? "available" : "not available"}</dd></div>
                <div><dt className="text-xs text-muted">Withdrawal-permission check</dt><dd>{ex.capabilities.withdrawal_permission_check}</dd></div>
              </dl>
            )}
            {ex.capabilities?.notes?.length > 0 && <ul className="mb-3 list-disc pl-5 text-xs text-muted">{ex.capabilities.notes.map((n: string) => <li key={n}>{n}</li>)}</ul>}
            <Table rows={ex.pairs} empty="No pairs synced yet (market-data service)." columns={[
              { key: "symbol", label: "Pair" }, { key: "exchange_symbol", label: "Native" },
              { key: "price_tick", label: "Tick", numeric: true, render: (p: Any) => qty(p.price_tick) },
              { key: "amount_step", label: "Step", numeric: true, render: (p: Any) => qty(p.amount_step) },
              { key: "min_notional", label: "Min notional", numeric: true, render: (p: Any) => qty(p.min_notional) },
              { key: "metadata_synced_at", label: "Synced", render: (p: Any) => time(p.metadata_synced_at) },
            ]} />
            <p className="mt-2 text-xs text-muted">API keys: {ex.credentials.length ? ex.credentials.map((c: Any) => `${c.label} (…${c.fingerprint})`).join(", ") : "none configured"}</p>
          </Card>
        ))}
      </div>
    </>
  );
}
