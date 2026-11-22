"use client";

import Link from "next/link";
import { useEffect } from "react";
import { Badge, Card, Loading, PageHeader, Stat, Table } from "@/components/ui";
import { num, pct, signed, time } from "@/lib/format";
import { useApi, useMode } from "@/lib/hooks";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

export default function PaperTrading() {
  const { setMode } = useMode();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => setMode("paper"), []);
  const o = useApi<Any>("/overview?mode=paper", 15_000);
  const q = useApi<Any>("/execution/quality?mode=paper", 60_000);
  const r = o.data?.report;
  return (
    <>
      <PageHeader title="Paper trading" subtitle="Simulated fills on real Binance prices with fees, slippage, order delay, partial fills and outages. Paper results are not real profits." />
      <Loading error={o.error} loading={o.loading && !o.data} />
      {o.data && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label="Paper equity" value={num(r?.equity)} hint={`started ${num(r?.initial_capital)}`} />
            <Stat label="Realized P&L" value={num(r?.realized_pnl)} tone={signed(r?.realized_pnl)} />
            <Stat label="Closed trades" value={String(r?.closed_trades ?? 0)} hint={`win rate ${pct(r?.win_rate, 1)}`} />
            <Stat label="Open positions" value={String(o.data.open_positions)} />
          </div>
          <Card title="Recent decisions">
            <Table rows={o.data.recent_signals} empty="No signals yet: no strategy is validated, so the paper trader is waiting." columns={[
              { key: "bar_time", label: "Bar", render: (x: Any) => time(x.bar_time) },
              { key: "status", label: "Decision", render: (x: Any) => <Badge tone={x.status === "executed" ? "good" : "muted"}>{x.status}</Badge> },
              { key: "rejection_reason", label: "Reason", render: (x: Any) => <span className="text-xs text-muted">{x.rejection_reason ?? "—"}</span> },
            ]} />
          </Card>
          {q.data?.quality && (
            <Card title="Execution quality (simulated)">
              <p className="text-sm">Entries: {q.data.quality.entry.orders} sent, fill rate {pct(q.data.quality.entry.fill_rate, 1)}, {q.data.quality.entry.missed} missed · Exits: {q.data.quality.exit.orders} sent. <Link className="underline" href="/performance/">Details</Link></p>
            </Card>
          )}
          <p className="text-xs text-muted">More: <Link className="underline" href="/positions/">positions</Link> · <Link className="underline" href="/trades/">trades</Link> · <Link className="underline" href="/proposals/">AI proposals</Link> · <Link className="underline" href="/risk/">risk center</Link></p>
        </div>
      )}
    </>
  );
}
