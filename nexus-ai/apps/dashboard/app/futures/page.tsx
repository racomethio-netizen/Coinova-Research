"use client";

import { AccountState, type AccountView } from "@/components/account";
import { Badge, Card, PageHeader, Stat, Table } from "@/components/ui";
import { num, pct, qty, signed, time } from "@/lib/format";
import { useApi } from "@/lib/hooks";

export default function FuturesPage() {
  const v = useApi<AccountView>("/account/futures", 15_000);
  return (
    <>
      <PageHeader title="Futures trading" subtitle="Binance USDT-M account: balances, positions, liquidation prices, funding and open orders, read live from Binance." />
      <AccountState view={v.data} error={v.error} loading={v.loading}>
        {(d) => (
          <>
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <Stat label="Wallet balance (USDT)" value={num(d.account.wallet_balance)} />
              <Stat label="Available (USDT)" value={num(d.account.available_balance)} />
              <Stat label="Unrealized P&L" value={num(d.account.unrealized_pnl)} tone={signed(d.account.unrealized_pnl)} />
              <Stat label="Margin in use" value={num(d.account.initial_margin)} hint={`maintenance ${num(d.account.maintenance_margin)}`} />
              <Stat label="Realized P&L (7d)" value={num(d.last_7_days.realized_pnl)} tone={signed(d.last_7_days.realized_pnl)} />
              <Stat label="Funding (7d)" value={num(d.last_7_days.funding)} tone={signed(d.last_7_days.funding)} hint="negative = paid" />
              <Stat label="Fees (7d)" value={num(d.last_7_days.commission)} />
              <Stat label="Open positions" value={String(d.positions.length)} />
            </div>
            <Card title="Positions">
              <Table rows={d.positions} empty="No open futures positions." columns={[
                { key: "symbol", label: "Symbol" },
                { key: "side", label: "Side", render: (p: AccountView) => <Badge tone={p.side === "long" ? "good" : "bad"}>{p.side}</Badge> },
                { key: "quantity", label: "Size", numeric: true, render: (p: AccountView) => qty(p.quantity) },
                { key: "entry_price", label: "Entry", numeric: true, render: (p: AccountView) => num(p.entry_price) },
                { key: "mark_price", label: "Mark", numeric: true, render: (p: AccountView) => num(p.mark_price) },
                { key: "liquidation_price", label: "Liquidation", numeric: true, render: (p: AccountView) => num(p.liquidation_price) },
                { key: "liquidation_distance", label: "To liquidation", numeric: true, render: (p: AccountView) => pct(p.liquidation_distance) },
                { key: "leverage", label: "Leverage", render: (p: AccountView) => `${p.leverage}x ${p.margin_type}` },
                { key: "unrealized_pnl", label: "Unrealized P&L", numeric: true, render: (p: AccountView) => num(p.unrealized_pnl) },
              ]} />
            </Card>
            <Card title="Open orders">
              <Table rows={d.open_orders} empty="No open futures orders." columns={[
                { key: "symbol", label: "Symbol" }, { key: "side", label: "Side" }, { key: "type", label: "Type" },
                { key: "quantity", label: "Qty", numeric: true, render: (o: AccountView) => o.close_position ? "close position" : qty(o.quantity) },
                { key: "price", label: "Price", numeric: true, render: (o: AccountView) => num(o.price) },
                { key: "stop_price", label: "Trigger", numeric: true, render: (o: AccountView) => num(o.stop_price) },
                { key: "reduce_only", label: "Reduce-only", render: (o: AccountView) => (o.reduce_only || o.close_position ? "yes" : "no") },
                { key: "time", label: "Placed", render: (o: AccountView) => (o.time ? time(new Date(o.time).toISOString()) : "—") },
              ]} />
            </Card>
          </>
        )}
      </AccountState>
    </>
  );
}
