"use client";

import { AccountState, type AccountView } from "@/components/account";
import { Badge, Card, PageHeader, Table } from "@/components/ui";
import { num, qty, time } from "@/lib/format";
import { useApi } from "@/lib/hooks";

export default function SpotPage() {
  const v = useApi<AccountView>("/account/spot", 15_000);
  return (
    <>
      <PageHeader title="Spot trading" subtitle="Binance spot account: assets, open orders and recent trades, read live from Binance." />
      <AccountState view={v.data} error={v.error} loading={v.loading}>
        {(d) => (
          <>
            <Card title="Assets">
              <Table rows={d.balances} empty="No assets with a balance." columns={[
                { key: "asset", label: "Asset" },
                { key: "free", label: "Available", numeric: true, render: (b: AccountView) => qty(b.free) },
                { key: "locked", label: "In orders", numeric: true, render: (b: AccountView) => qty(b.locked) },
                { key: "total", label: "Total", numeric: true, render: (b: AccountView) => qty(b.total) },
              ]} />
            </Card>
            <Card title="Open orders">
              <Table rows={d.open_orders} empty="No open spot orders." columns={[
                { key: "symbol", label: "Symbol" },
                { key: "side", label: "Side", render: (o: AccountView) => <Badge tone={o.side === "buy" ? "good" : "bad"}>{o.side}</Badge> },
                { key: "type", label: "Type" },
                { key: "price", label: "Price", numeric: true, render: (o: AccountView) => num(o.price) },
                { key: "amount", label: "Amount", numeric: true, render: (o: AccountView) => qty(o.amount) },
                { key: "filled", label: "Filled", numeric: true, render: (o: AccountView) => qty(o.filled) },
                { key: "time", label: "Placed", render: (o: AccountView) => time(o.time) },
              ]} />
            </Card>
            <Card title="Recent trades (configured pairs)">
              <Table rows={d.recent_trades} empty="No recent spot trades." columns={[
                { key: "time", label: "Time", render: (t: AccountView) => time(t.time) },
                { key: "symbol", label: "Symbol" },
                { key: "side", label: "Side" },
                { key: "price", label: "Price", numeric: true, render: (t: AccountView) => num(t.price) },
                { key: "amount", label: "Amount", numeric: true, render: (t: AccountView) => qty(t.amount) },
              ]} />
            </Card>
          </>
        )}
      </AccountState>
    </>
  );
}
