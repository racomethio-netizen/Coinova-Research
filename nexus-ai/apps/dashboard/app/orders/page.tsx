"use client";

import { Badge, Card, Loading, PageHeader, Table, statusTone } from "@/components/ui";
import { DASH, num, pct, qty, time } from "@/lib/format";
import { useApi, useMode } from "@/lib/hooks";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

export default function Orders() {
  const { mode } = useMode();
  const { data, error, loading } = useApi<Any[]>(`/orders?mode=${mode}`, 15000);
  return (
    <>
      <PageHeader title="Order history" subtitle="Every order with its client order ID (the idempotency key used for reconciliation)" />
      <Loading error={error} loading={loading && !data} />
      <Card>
        <Table rows={data} empty="No orders." columns={[
          { key: "created_at", label: "Created", render: (o: Any) => time(o.created_at) },
          { key: "exchange", label: "Exchange" }, { key: "symbol", label: "Pair" },
          { key: "side", label: "Side" }, { key: "purpose", label: "Purpose" }, { key: "time_in_force", label: "TIF" },
          { key: "price", label: "Limit", numeric: true, render: (o: Any) => num(o.price) },
          { key: "amount", label: "Amount", numeric: true, render: (o: Any) => qty(o.amount) },
          { key: "filled_amount", label: "Filled", numeric: true, render: (o: Any) => qty(o.filled_amount) },
          { key: "average_fill_price", label: "Avg fill", numeric: true, render: (o: Any) => num(o.average_fill_price) },
          { key: "fee_amount", label: "Fee", numeric: true, render: (o: Any) => `${qty(o.fee_amount)} ${o.fee_asset ?? ""}` },
          { key: "status", label: "Status", render: (o: Any) => <Badge tone={statusTone(o.status)}>{o.status}</Badge> },
          { key: "client_order_id", label: "Client ID", render: (o: Any) => <span className="num text-xs text-muted">{o.client_order_id.slice(0, 8)}…</span> },
          { key: "last_error", label: "Error", render: (o: Any) => o.last_error ?? DASH },
        ]} />
      </Card>
    </>
  );
}
