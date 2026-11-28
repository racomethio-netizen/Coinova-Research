"use client";

import { Badge, Card, Loading, PageHeader, Table } from "@/components/ui";
import { DASH, num, time } from "@/lib/format";
import { useApi } from "@/lib/hooks";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

const TONE: Record<string, "good" | "bad" | "muted"> = { BUY: "good", SELL: "bad", NO_TRADE: "muted" };

function Why({ d }: { d: Any }) {
  const lines: string[] = [...(d.reasons ?? [])];
  for (const c of d.candidates ?? []) {
    if (!c.eligible) lines.push(`${c.module} (${c.side}) not taken: ${c.why_not.join(", ")}`);
  }
  return <ul className="list-disc space-y-0.5 pl-4 text-xs">{lines.map((l, i) => <li key={i}>{l}</li>)}</ul>;
}

export default function OrchestratorPage() {
  const { data, error, loading } = useApi<Any>("/orchestrator", 30_000);
  const board: Any[] = data?.board ?? [];
  return (
    <>
      <PageHeader title="Strategy orchestrator" subtitle="One decision per instrument, forex and crypto: BUY, SELL or NO TRADE, with every reason" />
      <Loading error={error} loading={loading && !data} />
      {data && (
        <div className="space-y-4">
          <p className="rounded-lg border border-line bg-elevated p-3 text-sm">
            Only a strategy module that passed its test can propose a trade. Regime, volatility, liquidity, session and scheduled events can only block. A BUY or SELL here is a proposal: the independent risk engine and execution still decide, and nothing on this page places an order. Live trading is off.
          </p>
          <Card title={`Decisions${data.at ? ` (updated ${time(data.at)})` : ""}`}>
            <Table rows={board} empty="The orchestrator has not run yet (it runs every 5 minutes)." columns={[
              { key: "symbol", label: "Instrument" },
              { key: "market", label: "Market" },
              { key: "action", label: "Decision", render: (x: Any) => <Badge tone={TONE[x.action] ?? "muted"}>{x.action === "NO_TRADE" ? "NO TRADE" : x.action}</Badge> },
              { key: "module", label: "Strategy", render: (x: Any) => x.module ?? DASH },
              { key: "regime", label: "Regime", render: (x: Any) => x.regime ?? DASH },
              { key: "price", label: "Price", numeric: true, render: (x: Any) => num(x.snapshot?.price, x.market === "forex" ? 5 : 2) },
              { key: "why", label: "Why", render: (x: Any) => <Why d={x} /> },
            ]} />
          </Card>
          <Card title="Decision log (latest changes and hourly checks)">
            <Table rows={data.history ?? []} empty="No decisions recorded yet." columns={[
              { key: "decided_at", label: "When", render: (x: Any) => time(x.decided_at) },
              { key: "symbol", label: "Instrument" },
              { key: "action", label: "Decision", render: (x: Any) => <Badge tone={TONE[x.action] ?? "muted"}>{x.action === "NO_TRADE" ? "NO TRADE" : x.action}</Badge> },
              { key: "module", label: "Strategy", render: (x: Any) => x.module ?? DASH },
              { key: "reason", label: "Main reason", render: (x: Any) => <span className="text-xs">{(x.blockers?.[0] ?? x.reasons?.[0]) || DASH}</span> },
            ]} />
          </Card>
        </div>
      )}
    </>
  );
}
