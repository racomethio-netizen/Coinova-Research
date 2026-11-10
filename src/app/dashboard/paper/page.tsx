import type { Metadata } from "next";
import { LineChart } from "@/components/market/line-chart";
import { Price } from "@/components/nexus/bits";
import { Badge, Card, CardBody, CardHeader, EmptyState, Notice, PageHeader, Stat } from "@/components/ui";
import { requireUserPage } from "@/lib/auth/session";
import { fmtDate } from "@/lib/format";
import { listPairs } from "@/server/nexus";
import { getPaperSummary } from "@/server/paper";
import { CancelOrder, OpenAccount, OrderForm, Protection } from "./client";

export const metadata: Metadata = { title: "Paper trading" };

export default async function PaperPage() {
  const viewer = await requireUserPage("/dashboard/paper");
  const [summary, pairs] = await Promise.all([getPaperSummary(viewer.supabase, viewer.user.id), listPairs(viewer.supabase)]);
  return (
    <div className="space-y-6">
      <PageHeader title={<span className="flex items-center gap-3">Paper trading <Badge tone="warning">Virtual funds</Badge></span>} description="Practice with a virtual USDT balance. No real money, no exchange connection, no transfers. Long positions only." />
      {!summary.account ? (
        <Card><CardHeader title="Open a virtual account" /><CardBody><OpenAccount /></CardBody></Card>
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Card><CardBody><dl><Stat label="Equity (virtual)" value={summary.account.equity ? <Price v={summary.account.equity} /> : "—"} sub={summary.account.equity ? "Cash + positions at the latest price" : "A price is stale or missing, so equity is not estimated"} /></dl></CardBody></Card>
            <Card><CardBody><dl><Stat label="Cash (virtual)" value={<Price v={summary.account.cash} />} /></dl></CardBody></Card>
            <Card><CardBody><dl><Stat label="Realized P/L" value={<Price v={summary.account.realizedPnl} />} sub="Net of fees" /></dl></CardBody></Card>
            <Card><CardBody><dl><Stat label="Starting balance" value={<Price v={summary.account.starting_balance} />} sub={`Opened ${fmtDate(summary.account.created_at)}`} /></dl></CardBody></Card>
          </div>
          <Card><CardHeader title="New order" description="Orders fill against the latest public price (at most 60 s old), with 0.05% slippage and a 0.1% fee per fill." /><CardBody><OrderForm pairs={pairs.map((p) => ({ id: p.id, symbol: p.symbol }))} /></CardBody></Card>
          <Card className="overflow-x-auto">
            <CardHeader title="Positions" />
            {summary.account.positions.filter((p) => Number(p.quantity) > 0).length === 0 ? <CardBody><p className="text-sm text-muted">No open positions.</p></CardBody> : (
              <table className="w-full min-w-[760px] text-sm">
                <thead className="text-left text-xs uppercase text-faint"><tr>{["Pair", "Quantity", "Avg entry", "Price", "Unrealized", "Protection"].map((h) => <th key={h} scope="col" className="px-3 py-2">{h}</th>)}</tr></thead>
                <tbody className="divide-y divide-line">
                  {summary.account.positions.filter((p) => Number(p.quantity) > 0).map((p) => (
                    <tr key={p.pairId}>
                      <th scope="row" className="px-3 py-2 text-left">{p.symbol}</th>
                      <td className="px-3 py-2"><Price v={p.quantity} /></td>
                      <td className="px-3 py-2"><Price v={p.avgEntry} /></td>
                      <td className="px-3 py-2"><Price v={p.price} />{p.priceStale ? <Badge className="ml-1" tone="warning">stale</Badge> : null}</td>
                      <td className="px-3 py-2"><Price v={p.unrealizedPnl} /></td>
                      <td className="px-3 py-2"><Protection pairId={p.pairId} stopLoss={p.stopLoss} takeProfit={p.takeProfit} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>
          <div className="grid gap-4 lg:grid-cols-2">
            <Card className="overflow-x-auto">
              <CardHeader title="Orders" />
              <CardBody>
                {summary.orders?.length ? (
                  <ul className="divide-y divide-line text-sm">
                    {summary.orders.map((o) => (
                      <li key={o.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                        <span>{o.side.toUpperCase()} {o.order_type} <Price v={o.quantity} />{o.limit_price ? <> @ <Price v={o.limit_price} /></> : null}</span>
                        <span className="flex items-center gap-2">
                          <Badge tone={o.status === "filled" ? "positive" : o.status === "pending" ? "accent" : "neutral"}>{o.status}</Badge>
                          {o.reject_reason ? <span className="text-xs text-faint">{o.reject_reason}</span> : null}
                          {o.status === "pending" ? <CancelOrder id={o.id} /> : null}
                        </span>
                      </li>
                    ))}
                  </ul>
                ) : <p className="text-sm text-muted">No orders yet.</p>}
              </CardBody>
            </Card>
            <Card className="overflow-x-auto">
              <CardHeader title="Fills" />
              <CardBody>
                {summary.fills?.length ? (
                  <ul className="divide-y divide-line text-sm">
                    {summary.fills.map((f) => (
                      <li key={f.id} className="flex flex-wrap justify-between gap-2 py-2">
                        <span>{f.side.toUpperCase()} <Price v={f.quantity} /> @ <Price v={f.price} /> <span className="text-xs text-faint">({f.reason.replace("_", " ")}, fee <Price v={f.fee} />)</span></span>
                        <span className="text-xs text-muted">{fmtDate(f.created_at, { time: true })}</span>
                      </li>
                    ))}
                  </ul>
                ) : <p className="text-sm text-muted">No fills yet. Orders fill once a fresh price is available.</p>}
              </CardBody>
            </Card>
          </div>
          <Card>
            <CardHeader title="Equity history (virtual)" description="Hourly snapshots; a snapshot is skipped when any price is stale." />
            <CardBody><LineChart points={(summary.equityHistory ?? []).map((s) => ({ t: Date.parse(s.taken_at), v: Number(s.equity) }))} label="Virtual equity over time" empty="No snapshots yet." /></CardBody>
          </Card>
          <Card><CardHeader title="Reset" description="Archives this account (open orders are cancelled; history is kept) and opens a new one." /><CardBody><OpenAccount reset /></CardBody></Card>
        </>
      )}
      {!pairs.length ? <EmptyState title="No tradable pairs" description="No market pairs are enabled yet." /> : null}
      <Notice tone="neutral">Paper trading simulates fills; real execution can differ (liquidity, latency, partial fills). Nothing here is financial advice.</Notice>
    </div>
  );
}
