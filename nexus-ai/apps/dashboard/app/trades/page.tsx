"use client";

import { Badge, Card, Loading, PageHeader, Table, statusTone } from "@/components/ui";
import { DASH, num, pct, qty, time } from "@/lib/format";
import { useApi, useMode } from "@/lib/hooks";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

export default function Trades() {
  const { mode } = useMode();
  const { data, error, loading } = useApi<Any[]>(`/trades?mode=${mode}`, 15000);
  return (
    <>
      <PageHeader title="Trade history" subtitle={mode === "paper" ? "Simulated fills (IDs start with paper-)" : "Exchange-reported fills"} />
      <Loading error={error} loading={loading && !data} />
      <Card>
        <Table rows={data} empty="No fills." columns={[
          { key: "executed_at", label: "Time", render: (t: Any) => time(t.executed_at) },
          { key: "exchange", label: "Exchange" }, { key: "symbol", label: "Pair" }, { key: "side", label: "Side" },
          { key: "price", label: "Price", numeric: true, render: (t: Any) => num(t.price) },
          { key: "amount", label: "Amount", numeric: true, render: (t: Any) => qty(t.amount) },
          { key: "fee", label: "Fee", numeric: true, render: (t: Any) => `${qty(t.fee)} ${t.fee_asset ?? ""}` },
          { key: "liquidity", label: "Liquidity" },
          { key: "trade_id", label: "Fill ID", render: (t: Any) => <span className="num text-xs text-muted">{String(t.trade_id).slice(0, 14)}</span> },
        ]} />
      </Card>
    </>
  );
}
