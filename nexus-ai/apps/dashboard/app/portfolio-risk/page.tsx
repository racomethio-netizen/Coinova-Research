"use client";

import { Badge, Card, Loading, PageHeader, Stat, Table } from "@/components/ui";
import { DASH, num, pct, time } from "@/lib/format";
import { useApi } from "@/lib/hooks";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

export default function PortfolioRiskPage() {
  const { data, error, loading } = useApi<Any>("/portfolio-risk", 30_000);
  const lim = data?.limits ?? {};
  return (
    <>
      <PageHeader title="Portfolio risk" subtitle="One risk book for forex and crypto: every proposed trade is approved or rejected here" />
      <Loading error={error} loading={loading && !data} />
      {data && (
        <div className="space-y-4">
          <p className="rounded-lg border border-line bg-elevated p-3 text-sm">
            Limits: {pct(lim.risk_per_trade, 2)} of the account at risk per trade, {pct(lim.daily_loss, 1)} daily loss, {pct(lim.weekly_loss, 1)} weekly loss, trading paused at {pct(lim.drawdown_pause, 0)} drawdown (only you can resume), at most {lim.max_positions} positions, at least {lim.min_reward_risk}:1 planned reward/risk, all open risk at most {pct(lim.max_open_risk, 1)}, positions that move together at most {pct(lim.max_cluster_risk, 1)}, no leverage. Paper and demo money only.
          </p>
          {data.note && <p className="text-sm text-muted">{data.note}</p>}
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Stat label="Total equity (USD)" value={num(data.equity, 2)} />
            <Stat label="Today" value={data.day_loss == null ? DASH : pct(-Number(data.day_loss), 2)} />
            <Stat label="This week" value={data.week_loss == null ? DASH : pct(-Number(data.week_loss), 2)} />
            <Stat label="Drawdown from peak" value={pct(data.drawdown, 2)} tone={Number(data.drawdown ?? 0) >= Number(lim.drawdown_pause ?? 1) * 0.8 ? "warn" : undefined} />
          </div>
          <Card title={`Checks${data.at ? ` (${time(data.at)})` : ""}`}>
            <Table rows={data.checks ?? []} empty="No checks yet." columns={[
              { key: "name", label: "Check", render: (x: Any) => x.name.replaceAll("_", " ") },
              { key: "passed", label: "Status", render: (x: Any) => <Badge tone={x.passed ? "good" : "bad"}>{x.passed ? "ok" : "blocks new trades"}</Badge> },
              { key: "detail", label: "Detail" },
            ]} />
          </Card>
          {(data.accounts ?? []).map((a: Any) => (
            <Card key={a.name} title={`${a.name === "crypto_paper" ? "Crypto paper book" : "Exness demo account"}: ${num(a.equity, 2)} USD`} actions={<Badge tone={a.reconciled ? "good" : "bad"}>{a.reconciled ? "reconciled" : "not reconciled: entries blocked"}</Badge>}>
              <p className="mb-2 text-xs text-muted">{a.note}</p>
              <Table rows={a.positions ?? []} empty="No open positions." columns={[
                { key: "symbol", label: "Instrument" },
                { key: "side", label: "Side", render: (x: Any) => x.side > 0 ? "long" : "short" },
                { key: "quantity", label: "Units", numeric: true, render: (x: Any) => num(x.quantity, 4) },
                { key: "entry", label: "Entry", numeric: true },
                { key: "stop", label: "Stop", numeric: true, render: (x: Any) => x.stop ?? "none (full value at risk)" },
                { key: "risk_usd", label: "At risk (USD)", numeric: true },
                { key: "notional_usd", label: "Value (USD)", numeric: true },
              ]} />
            </Card>
          ))}
          <Card title="Measured correlations (last 90 days)">
            <Table rows={data.correlations ?? []} empty="Not enough stored daily prices yet." columns={[
              { key: "a", label: "Instrument" },
              { key: "b", label: "With" },
              { key: "rho", label: "Correlation", numeric: true, render: (x: Any) => num(x.rho, 2) },
            ]} />
            <p className="mt-2 text-xs text-muted">Positions whose returns move together (0.7 or more in the same direction) count as one risk. Where no correlation is measured, the same currency exposed on the same side counts as moving together.</p>
          </Card>
        </div>
      )}
    </>
  );
}
