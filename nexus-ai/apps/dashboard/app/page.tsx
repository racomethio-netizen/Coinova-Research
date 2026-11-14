"use client";

import { Badge, Card, Loading, PageHeader, Stat, Table, statusTone } from "@/components/ui";
import { DASH, num, pct, signed, time } from "@/lib/format";
import { useApi, useMode } from "@/lib/hooks";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

export default function Overview() {
  const { mode } = useMode();
  const { data, error, loading } = useApi<Any>(`/overview?mode=${mode}`, 15000);
  const r = data?.report;
  return (
    <>
      <PageHeader title="Overview" subtitle={r?.title ?? `${mode.toUpperCase()} portfolio`} />
      <Loading error={error} loading={loading && !data} />
      {data && (
        <div className="space-y-4">
          {data.kill_switch && (
            <p role="alert" className="rounded-lg border border-down/50 bg-down/10 p-3 text-sm text-down">
              Kill switch engaged: {data.kill_switch.reason} ({data.kill_switch.actor}, {time(data.kill_switch.at)}). No new orders.
            </p>
          )}
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label="Portfolio value" value={num(r?.equity)} hint={data.config.quote_currency} />
            <Stat label="Available balance" value={num(r?.cash)} />
            <Stat label="Total P&L (realized + unrealized)" value={r ? num(String(Number(r.realized_pnl) + Number(r.unrealized_pnl ?? 0))) : DASH} tone={signed(r?.realized_pnl)} />
            <Stat label="Realized P&L" value={num(r?.realized_pnl)} tone={signed(r?.realized_pnl)} />
            <Stat label="Unrealized P&L" value={num(r?.unrealized_pnl)} tone={signed(r?.unrealized_pnl)} />
            <Stat label="Win rate" value={pct(r?.win_rate, 1)} hint={`${r?.closed_trades ?? 0} closed trades`} />
            <Stat label="Max drawdown" value={pct(r?.max_drawdown)} tone={Number(r?.max_drawdown ?? 0) > 0.03 ? "warn" : undefined} />
            <Stat label="Profit factor" value={num(r?.profit_factor)} />
            <Stat label="Open trades" value={String(data.open_positions)} />
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <Card title="Exchange status">
              <ul className="space-y-2 text-sm">
                {Object.entries(data.exchanges as Record<string, Any>).map(([code, h]) => (
                  <li key={code} className="flex justify-between">
                    <span className="capitalize">{code}</span>
                    {h ? <Badge tone={h.ok ? "good" : "bad"}>{h.ok ? `up · ${h.latency_ms ?? DASH} ms` : "down"}</Badge> : <Badge>no data</Badge>}
                  </li>
                ))}
              </ul>
            </Card>
            <Card title="Strategies and AI">
              <ul className="space-y-2 text-sm">
                {data.strategies.map((s: Any) => (
                  <li key={s.code} className="flex justify-between">
                    <span>{s.code}</span>
                    <span className="flex gap-1">
                      <Badge tone={s.validated ? "good" : "bad"}>{s.validated ? "validated" : "failed validation"}</Badge>
                      <Badge tone={s.enabled ? "good" : "muted"}>{s.enabled ? "enabled" : "disabled"}</Badge>
                    </span>
                  </li>
                ))}
                <li className="flex justify-between border-t border-line pt-2">
                  <span>AI model</span>
                  {data.latest_model ? <Badge tone={statusTone(data.latest_model.status)}>{`${data.latest_model.name} v${data.latest_model.version}: ${data.latest_model.status}`}</Badge> : <Badge>none</Badge>}
                </li>
              </ul>
            </Card>
          </div>
          <Card title="Latest signals">
            <Table rows={data.recent_signals} empty="No signals yet. Only strategies that passed out-of-sample validation can generate them." columns={[
              { key: "bar_time", label: "Bar", render: (x: Any) => time(x.bar_time) },
              { key: "side", label: "Side" },
              { key: "entry_price", label: "Entry", numeric: true, render: (x: Any) => num(x.entry_price) },
              { key: "confidence", label: "Confidence", numeric: true, render: (x: Any) => pct(x.confidence, 0) },
              { key: "status", label: "Status", render: (x: Any) => <Badge tone={statusTone(x.status)}>{x.status}</Badge> },
              { key: "rejection_reason", label: "Reason" },
            ]} />
          </Card>
        </div>
      )}
    </>
  );
}
