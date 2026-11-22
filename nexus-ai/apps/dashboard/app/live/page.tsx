"use client";

import Link from "next/link";
import { Badge, Card, Loading, PageHeader } from "@/components/ui";
import { useApi } from "@/lib/hooks";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

export default function LiveTrading() {
  const { data, error, loading } = useApi<Any>("/settings/summary");
  const blockers: string[] = data?.live_trading_blockers ?? [];
  return (
    <>
      <PageHeader title="Live trading" subtitle="Real money. Disabled until every owner gate passes and a strategy has passed validation and a paper period." />
      <Loading error={error} loading={loading && !data} />
      {data && (
        <div className="space-y-4">
          <Card title="Status" actions={<Badge tone={blockers.length ? "good" : "bad"}>{blockers.length ? "LIVE TRADING OFF" : "permitted by config"}</Badge>}>
            {blockers.length > 0 && (
              <>
                <p className="mb-2 text-sm">Blocking gates (each must be cleared deliberately by the owner):</p>
                <ul className="list-disc pl-5 text-sm text-warn">{blockers.map((b) => <li key={b}>{b}</li>)}</ul>
              </>
            )}
            <p className="mt-3 text-xs text-muted">The owner approval (typed phrase + two-factor code) is on the <Link className="underline" href="/security/">Security</Link> page. No strategy is validated today, so even with every gate cleared the trader would place no orders.</p>
          </Card>
          <div className="grid gap-4 md:grid-cols-2">
            <Card title="Spot account"><p className="text-sm">Balances, open orders and fills read from Binance. <Link className="underline" href="/spot/">Open</Link></p></Card>
            <Card title="Futures account"><p className="text-sm">Futures stay disabled for live execution. <Link className="underline" href="/futures/">Open</Link></p></Card>
          </div>
        </div>
      )}
    </>
  );
}
