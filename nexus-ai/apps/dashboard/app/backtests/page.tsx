"use client";

import { useState } from "react";
import { Badge, Card, Loading, PageHeader, Table } from "@/components/ui";
import { DASH, num, pct, time } from "@/lib/format";
import { useApi } from "@/lib/hooks";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

const MARKETS = ["all", "spot", "futures"] as const;

export default function BacktestsPage() {
  const [market, setMarket] = useState<(typeof MARKETS)[number]>("all");
  const [open, setOpen] = useState<number | null>(null);
  const r = useApi<{ runs: Any[] }>(`/backtests?market=${market}`);
  const runs = r.data?.runs ?? [];
  const selected = runs.find((x: Any) => x.id === open);
  return (
    <>
      <PageHeader title="Backtesting" subtitle="Walk-forward validation on real historical Binance data. Parameters are chosen on each training window and judged only on the next, unseen window, after fees, slippage and (futures) real funding. Past results do not predict future returns." />
      <div role="group" aria-label="Market" className="mb-4 flex w-fit rounded-lg border border-line p-0.5 text-xs">
        {MARKETS.map((m) => (
          <button key={m} type="button" aria-pressed={market === m} onClick={() => { setMarket(m); setOpen(null); }}
            className={`rounded-md px-3 py-1 capitalize ${market === m ? "bg-elevated text-text" : "text-muted"}`}>{m}</button>
        ))}
      </div>
      <Loading error={r.error} loading={r.loading && !r.data} />
      <div className="space-y-4">
        <Card title="Runs (out-of-sample)">
          <Table rows={runs} empty="No backtest runs recorded yet." columns={[
            { key: "strategy", label: "Strategy", render: (x: Any) => (
              <button type="button" className="underline" onClick={() => setOpen(open === x.id ? null : x.id)}>{x.strategy ?? DASH}</button>) },
            { key: "market", label: "Market", render: (x: Any) => <Badge tone={x.market === "futures" ? "info" : "muted"}>{x.market}</Badge> },
            { key: "passed", label: "Verdict", render: (x: Any) => <Badge tone={x.passed ? "good" : "bad"}>{x.passed ? "passed" : "failed"}</Badge> },
            { key: "n", label: "Trades", numeric: true, render: (x: Any) => x.summary?.n ?? DASH },
            { key: "win", label: "Win rate", numeric: true, render: (x: Any) => pct(x.summary?.win_rate) },
            { key: "exp", label: "Expectancy / trade", numeric: true, render: (x: Any) => pct(x.summary?.expectancy, 3) },
            { key: "pf", label: "Profit factor", numeric: true, render: (x: Any) => num(x.summary?.profit_factor) },
            { key: "pw", label: "Positive windows", numeric: true, render: (x: Any) => pct(x.summary?.positive_window_share, 0) },
            { key: "period", label: "Data", render: (x: Any) => `${(x.data_start ?? "").slice(0, 10)} → ${(x.data_end ?? "").slice(0, 10)}` },
          ]} />
        </Card>
        {selected && (
          <Card title={`${selected.strategy} · ${selected.market} · run ${selected.id}`}>
            <ul className="mb-3 space-y-1 text-sm">
              <li>Symbols: {(selected.symbols ?? []).join(", ") || DASH} · {selected.side} · fee {num(selected.fee_bps, 1)} bps + slippage {num(selected.slippage_bps, 1)} bps per side</li>
              {selected.funding && <li>Funding: {selected.funding}; out-of-sample funding paid: {pct(selected.oos_funding_paid, 3)} of notional (sum over trades)</li>}
              <li>Windows: train {selected.train_days ?? DASH} days, test {selected.test_days ?? DASH} days · recorded {time(selected.completed_at)}</li>
              {!selected.passed && <li className="text-down">Failed gates: {(selected.failures ?? []).join("; ")}</li>}
            </ul>
            <Table rows={selected.windows ?? []} empty="No windows." columns={[
              { key: "test_start", label: "Test window", render: (w: Any) => `${String(w.test_start).slice(0, 10)} → ${String(w.test_end).slice(0, 10)}` },
              { key: "params", label: "Chosen on train", render: (w: Any) => w.params ? Object.entries(w.params).map(([k, v]) => `${k}=${v}`).join(", ") : "no parameters met the trade minimum" },
              { key: "n", label: "Test trades", numeric: true, render: (w: Any) => w.test?.n ?? DASH },
              { key: "exp", label: "Test expectancy", numeric: true, render: (w: Any) => pct(w.test?.expectancy, 3) },
              { key: "pf", label: "Test PF", numeric: true, render: (w: Any) => num(w.test?.profit_factor) },
            ]} />
          </Card>
        )}
      </div>
    </>
  );
}
