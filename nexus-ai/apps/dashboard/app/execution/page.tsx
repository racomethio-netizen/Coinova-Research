"use client";

import { Badge, Card, Loading, PageHeader, Table } from "@/components/ui";
import { DASH, num, time } from "@/lib/format";
import { useApi } from "@/lib/hooks";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

const TONE: Record<string, "good" | "bad" | "warn" | "muted" | "info"> = {
  pending: "info", sent: "info", filled: "warn", protected: "good", closed: "muted", rejected: "bad", expired: "muted",
};

export default function ExecutionPage() {
  const { data, error, loading } = useApi<Any>("/execution", 30_000);
  const sum = data?.summary ?? {};
  return (
    <>
      <PageHeader title="Execution" subtitle="Paper and demo orders from approved proposals: sent once, stop-loss verified, slippage measured" />
      <Loading error={error} loading={loading && !data} />
      {data && (
        <div className="space-y-4">
          <p className="rounded-lg border border-line bg-elevated p-3 text-sm">
            An order is sent only after the orchestrator proposed it, the portfolio risk engine approved it, the market&apos;s switch is PAPER and the kill switch is clear. Crypto orders go to the paper book; forex and gold orders go to your Exness <strong>demo</strong> account through the demo trader on your PC. Each order has its own ID and is never sent twice; after the fill its stop-loss is checked on the broker (restored if missing, or the position is closed). Real-money trading is off.
          </p>
          <p className="text-sm">{sum.total ?? 0} orders · {Object.entries(sum.by_status ?? {}).map(([k, v]) => `${v} ${k}`).join(", ") || "none yet"} · average slippage {sum.avg_slippage_bps == null ? DASH : `${num(sum.avg_slippage_bps, 1)} bps`}</p>
          <Card title="Orders">
            <Table rows={data.intents ?? []} empty="No orders yet. Only modules that passed their test can propose one." columns={[
              { key: "created_at", label: "Created", render: (x: Any) => time(x.created_at) },
              { key: "symbol", label: "Instrument", render: (x: Any) => `${x.symbol} (${x.venue === "crypto_paper" ? "crypto paper" : "Exness demo"})` },
              { key: "side", label: "Side" },
              { key: "module", label: "Strategy" },
              { key: "status", label: "Status", render: (x: Any) => <Badge tone={TONE[x.status] ?? "muted"}>{x.status}</Badge> },
              { key: "fill", label: "Fill", numeric: true, render: (x: Any) => x.fill_price ?? DASH },
              { key: "slip", label: "Slippage", numeric: true, render: (x: Any) => x.slippage_bps == null ? DASH : `${num(x.slippage_bps, 1)} bps` },
              { key: "stop", label: "Stop verified", render: (x: Any) => x.kind === "allocation" ? "no stop (allocation)" : x.stop_verified ? "yes" : x.stop_verified === false ? "no" : DASH },
              { key: "last", label: "Last event", render: (x: Any) => <span className="text-xs">{x.events?.[x.events.length - 1]?.detail ?? DASH}</span> },
            ]} />
          </Card>
        </div>
      )}
    </>
  );
}
