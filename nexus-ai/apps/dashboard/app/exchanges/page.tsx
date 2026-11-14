"use client";

import { Badge, Card, Loading, PageHeader, Table, statusTone } from "@/components/ui";
import { DASH, num, pct, qty, time } from "@/lib/format";
import { useApi, useMode } from "@/lib/hooks";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

export default function Exchanges() {
  const { data, error, loading } = useApi<Any[]>("/exchanges", 30000);
  return (
    <>
      <PageHeader title="Exchanges" subtitle="Connectivity, verified capabilities, synced markets and API keys" />
      <Loading error={error} loading={loading && !data} />
      <div className="space-y-4">
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
