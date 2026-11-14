"use client";

import { Badge, Card, Loading, PageHeader, Table, statusTone } from "@/components/ui";
import { DASH, num, pct, qty, time } from "@/lib/format";
import { useApi, useMode } from "@/lib/hooks";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

export default function Positions() {
  const { mode } = useMode();
  const open = useApi<Any[]>(`/positions?mode=${mode}&status=open`, 10000);
  const closed = useApi<Any[]>(`/positions?mode=${mode}&status=closed`, 30000);
  const cols = [
    { key: "opened_at", label: "Opened", render: (p: Any) => time(p.opened_at) },
    { key: "exchange", label: "Exchange" }, { key: "symbol", label: "Pair" },
    { key: "quantity", label: "Qty", numeric: true, render: (p: Any) => qty(p.quantity) },
    { key: "avg_entry_price", label: "Avg entry", numeric: true, render: (p: Any) => num(p.avg_entry_price) },
    { key: "stop_loss", label: "Stop", numeric: true, render: (p: Any) => num(p.stop_loss) },
    { key: "take_profit", label: "Target", numeric: true, render: (p: Any) => num(p.take_profit) },
    { key: "realized_pnl", label: "Realized", numeric: true, render: (p: Any) => num(p.realized_pnl) },
    { key: "exit_reason", label: "Exit", render: (p: Any) => p.exit_reason ?? DASH },
    { key: "status", label: "Status", render: (p: Any) => <Badge tone={statusTone(p.status)}>{p.status}</Badge> },
  ];
  return (
    <>
      <PageHeader title="Open positions" subtitle={`${mode.toUpperCase()} · long-only spot, max 2 concurrent`} />
      <Loading error={open.error} loading={open.loading && !open.data} />
      <div className="space-y-4">
        <Card title="Open"><Table rows={open.data} columns={cols} empty="No open positions." /></Card>
        <Card title="Recently closed"><Table rows={closed.data} columns={cols} empty="No closed positions." /></Card>
      </div>
    </>
  );
}
