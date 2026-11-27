"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { Badge, Button, Card, Field, Loading, PageHeader, Table } from "@/components/ui";
import { DASH, num, pct, time } from "@/lib/format";
import { useApi } from "@/lib/hooks";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

const GATE: Record<string, string> = {
  enough_trades: "enough trades",
  positive_expectancy: "positive expectancy after costs",
  significant_after_holm: "statistically significant",
  profit_factor: "profit factor ≥ 1.15",
  both_halves_positive: "positive in both halves",
  positive_at_2x_costs: "positive at 2× costs",
  max_drawdown: "drawdown ≤ 10%",
};

function verdict(m: Any): { label: string; tone: "good" | "bad" | "muted" } {
  const v = m.validation;
  if (!v) return { label: "not tested", tone: "muted" };
  if (m.evidence === "earlier_pass") return { label: v.passed ? "passed (23 Nov)" : "failed", tone: v.passed ? "good" : "bad" };
  if (m.evidence === "earlier_fail") return { label: "failed (23 Nov)", tone: "bad" };
  return { label: v.passed ? "passed" : "failed", tone: v.passed ? "good" : "bad" };
}

function Switch({ m, reload }: { m: Any; reload: () => Promise<void> }) {
  const [reason, setReason] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const set = async (activation: string) => {
    setMsg(null);
    try { await api.post(`/strategy-modules/${m.code}/activation`, { activation, reason }); setReason(""); await reload(); } catch (e) { setMsg(e instanceof Error ? e.message : String(e)); }
  };
  if (!m.observable) {
    return <p className="text-sm">This module already runs as a forward <strong>paper</strong> test (no real money): see <a className="underline" href="/trend-test/">Trend strategy test</a>.</p>;
  }
  const next = m.activation === "OBSERVE" ? "OFF" : "OBSERVE";
  return (
    <div className="space-y-2 text-sm">
      <p>Switch: <strong>{m.activation}</strong>{m.activated_at ? ` since ${time(m.activated_at)}` : ""}. OBSERVE records the signals this module gives on new data and what each would have done. It places no order, paper or real. Paper trading of modules comes with the orchestrator, the shared risk book and the execution adapters, and only for a module that passed.</p>
      <div className="grid gap-3 md:grid-cols-3">
        <Field id={`reason-${m.code}`} label="Reason (owner only, recorded)" value={reason} onChange={setReason} />
        <div className="flex items-end"><Button disabled={reason.trim().length < 5} onClick={() => set(next)}>{next === "OBSERVE" ? "Start observing" : "Switch off"}</Button></div>
      </div>
      {msg && <p role="alert" className="text-down">{msg}</p>}
    </div>
  );
}

function Module({ m, reload }: { m: Any; reload: () => Promise<void> }) {
  const v = m.validation ?? {};
  const vd = verdict(m);
  const failed = Object.entries(v.gates ?? {}).filter(([, ok]) => !ok).map(([k]) => GATE[k] ?? k);
  const f = m.forward ?? {};
  return (
    <Card title={m.name} actions={<Badge tone={vd.tone}>{vd.label}</Badge>}>
      <p className="text-sm text-muted">{m.market === "forex" ? "Forex" : "Crypto (spot, buys only)"} · {m.style} · {m.symbols.join(", ")} · signals on {m.signal_timeframe}, managed on {m.execution_timeframe}</p>
      {m.note && <p className="mt-1 text-sm">{m.note}</p>}
      {m.code === "crypto_swing_trend" ? (
        <p className="mt-2 text-sm">Test (Aug 2018 – Nov 2026, BTC + ETH, after costs): Sharpe {num(v.sharpe, 2)}, {pct(v.cagr, 1)} a year, worst drop {pct(v.max_drawdown, 1)}.</p>
      ) : (
        <div className="mt-2 grid gap-1 text-sm sm:grid-cols-2">
          <p>Trades in the test: {v.trades ?? DASH} ({v.period ? `${v.period[0].slice(0, 10)} to ${v.period[1].slice(0, 10)}` : DASH})</p>
          <p>Win rate: {pct(v.win_rate, 1)}</p>
          <p>Average per trade after costs: <strong>{num(v.mean_r, 3)} R</strong></p>
          <p>Before costs: {num(v.mean_r_before_costs, 3)} R; at 2× costs: {num(v.mean_r_2x_costs, 3)} R</p>
          <p>Profit factor: {num(v.profit_factor, 2)}</p>
          <p>Worst drop at 0.25% risk a trade: {pct(v.max_drawdown, 1)}</p>
        </div>
      )}
      {failed.length > 0 && <p className="mt-1 text-xs text-muted">Missed: {failed.join(", ")}.</p>}
      <details className="mt-2 text-sm">
        <summary className="cursor-pointer text-muted">Settings</summary>
        <ul className="mt-1 grid gap-x-4 text-xs sm:grid-cols-2">
          {Object.entries(m.config ?? {}).map(([k, x]) => <li key={k}><span className="text-muted">{k.replaceAll("_", " ")}:</span> {Array.isArray(x) ? x.join(", ") : String(x)}</li>)}
        </ul>
      </details>
      <div className="mt-3 border-t border-line pt-3"><Switch m={m} reload={reload} /></div>
      {m.observable && (
        <div className="mt-3 border-t border-line pt-3 text-sm">
          <p className="mb-1 font-medium">Forward record (observation, no orders)</p>
          <p className="text-muted">{f.signals ?? 0} signals: {f.closed ?? 0} finished, {f.open ?? 0} open, {f.dropped ?? 0} dropped. {f.closed ? `Win rate ${pct(f.win_rate, 0)}, average ${num(f.mean_r, 2)} R, total ${num(f.total_r, 1)} R.` : "Nothing finished yet."}</p>
          {(f.recent ?? []).length > 0 && (
            <Table rows={f.recent} empty="" columns={[
              { key: "signal_time", label: "Signal", render: (x: Any) => time(x.signal_time) },
              { key: "symbol", label: "Symbol" },
              { key: "side", label: "Side" },
              { key: "status", label: "Status", render: (x: Any) => x.note ? `${x.status} (${x.note})` : x.status },
              { key: "reason", label: "Exit", render: (x: Any) => x.reason ?? DASH },
              { key: "net_r", label: "Result", numeric: true, render: (x: Any) => x.net_r == null ? DASH : `${num(x.net_r, 2)} R` },
            ]} />
          )}
        </div>
      )}
    </Card>
  );
}

export default function StrategyModulesPage() {
  const { data, error, loading, reload } = useApi<Any>("/strategy-modules", 30_000);
  const rows: Any[] = data?.modules ?? [];
  return (
    <>
      <PageHeader title="Strategy modules" subtitle="Six modules: forex and crypto; scalping, intraday and swing. Each with its own settings, test, record and switch." />
      <Loading error={error} loading={loading && !data} />
      {data && (
        <div className="space-y-4">
          <p className="rounded-lg border border-line bg-elevated p-3 text-sm">
            Every module plans at least 2:1 reward/risk, risks 0.25% a trade and holds at most 3 positions. Each was tested once on real prices after costs, with rules written down first ({data.preregistration}). <strong>Only the crypto swing module has passed.</strong> The four new modules and the breakout retest lost money after costs. No module trades; live trading stays off.
          </p>
          <Card title="Summary">
            <Table rows={rows} empty="No modules." columns={[
              { key: "name", label: "Module" },
              { key: "market", label: "Market" },
              { key: "style", label: "Style" },
              { key: "v", label: "Test", render: (x: Any) => { const v = verdict(x); return <Badge tone={v.tone}>{v.label}</Badge>; } },
              { key: "r", label: "Avg per trade", numeric: true, render: (x: Any) => x.code === "crypto_swing_trend" ? `Sharpe ${num(x.validation?.sharpe, 2)}` : `${num(x.validation?.mean_r, 3)} R` },
              { key: "a", label: "Switch", render: (x: Any) => x.observable ? x.activation : "forward paper test" },
            ]} />
            {data.observer && <p className="mt-2 text-xs text-muted">Observer last ran {time(data.observer.at)}: {data.observer.modules} module(s) observing.</p>}
          </Card>
          {rows.map((m: Any) => <Module key={m.code} m={m} reload={reload} />)}
        </div>
      )}
    </>
  );
}
