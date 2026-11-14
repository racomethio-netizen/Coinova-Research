"use client";

import { EquityChart } from "@/components/charts";
import { Card, Loading, PageHeader, Stat, Table } from "@/components/ui";
import { num, pct, qty, signed, time } from "@/lib/format";
import { useApi, useMode } from "@/lib/hooks";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

export default function Portfolio() {
  const { mode } = useMode();
  const { data, error, loading } = useApi<Any>(`/portfolio?mode=${mode}`, 30000);
  const r = data?.portfolio;
  return (
    <>
      <PageHeader title="Portfolio" subtitle={r?.title ?? `No ${mode} portfolio yet`} />
      <Loading error={error} loading={loading && !data} />
      {data && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label="Equity" value={num(r?.equity)} />
            <Stat label="Cash" value={num(r?.cash)} />
            <Stat label="Exposure" value={num(r?.exposure)} />
            <Stat label="Current drawdown" value={pct(r?.current_drawdown)} tone={signed(r?.current_drawdown) === "up" ? "warn" : undefined} />
            {mode === "paper" && <Stat label="Virtual starting capital" value={num(r?.initial_capital)} hint="Paper only" />}
          </div>
          <Card title="Equity curve">
            {data.equity_curve.length > 1 ? <EquityChart points={data.equity_curve} label={`${mode} equity curve`} /> : <p className="py-6 text-center text-sm text-muted">Not enough snapshots yet.</p>}
          </Card>
          <Card title="Balances by exchange">
            <Table rows={data.balances} empty="No balances recorded." columns={[
              { key: "exchange", label: "Exchange" }, { key: "asset", label: "Asset" },
              { key: "free", label: "Free", numeric: true, render: (b: Any) => qty(b.free) },
              { key: "locked", label: "Locked", numeric: true, render: (b: Any) => qty(b.locked) },
              { key: "source", label: "Source" },
              { key: "synced_at", label: "Updated", render: (b: Any) => time(b.synced_at) },
            ]} />
          </Card>
        </div>
      )}
    </>
  );
}
