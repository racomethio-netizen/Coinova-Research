"use client";

import { useState } from "react";
import { Badge, Card, Loading, PageHeader } from "@/components/ui";
import { DASH, time } from "@/lib/format";
import { useApi } from "@/lib/hooks";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

const TONE: Record<string, "good" | "warn" | "bad" | "muted"> = { LONG: "good", SHORT: "bad", WAIT: "warn", NO_TRADE: "muted" };
const LABEL: Record<string, string> = { LONG: "LONG", SHORT: "SHORT", WAIT: "WAIT", NO_TRADE: "NO TRADE" };
const FILTERS = ["all", "LONG", "SHORT", "WAIT", "NO_TRADE"] as const;

function fmt(v: unknown): string {
  if (v === null || v === undefined) return DASH;
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(4);
  if (typeof v === "boolean") return v ? "yes" : "no";
  return String(v);
}

function KeyValues({ data }: { data: Record<string, unknown> | null | undefined }) {
  const entries = Object.entries(data ?? {}).filter(([, v]) => typeof v !== "object" || v === null);
  if (!entries.length) return <p className="text-xs text-muted">{DASH}</p>;
  return (
    <dl className="grid grid-cols-2 gap-x-3 gap-y-0.5 text-xs md:grid-cols-4">
      {entries.map(([k, v]) => (
        <div key={k} className="contents"><dt className="text-muted">{k.replaceAll("_", " ")}</dt><dd className="num">{fmt(v)}</dd></div>
      ))}
    </dl>
  );
}

function Decision({ d }: { d: Any }) {
  const strategies = Object.entries((d.strategy_signals?.strategies ?? {}) as Record<string, Any>);
  const eligible = strategies.filter(([, v]) => v.eligible).map(([k]) => k);
  return (
    <Card title={`${d.symbol} · bar ${time(d.bar_time)}`} actions={<Badge tone={TONE[d.decision] ?? "muted"}>{LABEL[d.decision] ?? d.decision}</Badge>}>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-sm md:grid-cols-4">
        <dt className="text-muted">Decided</dt><dd>{time(d.decided_at)}</dd>
        <dt className="text-muted">Regime</dt><dd>{d.regime ?? DASH}</dd>
        <dt className="text-muted">Risk state</dt><dd>{d.risk_state ?? "unknown"}</dd>
        <dt className="text-muted">Strategy</dt><dd>{d.strategy ?? DASH}</dd>
      </dl>
      {d.rejection_reason && <p className="mt-2 text-sm"><span className="font-medium">Why not traded: </span>{d.rejection_reason}</p>}
      <p className="mt-1 text-sm"><span className="font-medium">Execution result: </span>{d.execution_result}</p>
      {d.execution?.length > 0 && (
        <ul className="mt-1 list-disc pl-5 text-xs">
          {d.execution.map((e: Any, i: number) => (
            <li key={i}>{e.strategy}: signal {e.signal_status}{e.rejection_reason ? ` (${e.rejection_reason})` : ""}{e.order_id ? `, order #${e.order_id} ${e.order_status}` : ", no order"}</li>
          ))}
        </ul>
      )}
      <details className="mt-2 text-sm">
        <summary className="cursor-pointer text-muted">Inputs, models, strategies and risk checks</summary>
        <div className="mt-2 space-y-3">
          <div>
            <p className="text-xs font-medium text-muted">Market data snapshot</p>
            <p className="num break-all text-xs">{d.snapshot_id}</p>
          </div>
          <div><p className="text-xs font-medium text-muted">Context inputs</p><KeyValues data={d.features?.context} /></div>
          <div><p className="text-xs font-medium text-muted">Multi-timeframe scores</p><KeyValues data={d.features?.mtf_scores} /></div>
          <div><p className="text-xs font-medium text-muted">Regime by timeframe</p><KeyValues data={d.features?.regimes_by_timeframe} /></div>
          <div><p className="text-xs font-medium text-muted">Model</p><KeyValues data={d.models_used} /></div>
          <div>
            <p className="text-xs font-medium text-muted">Risk checks</p>
            <p className="text-xs">Entries allowed: {fmt(d.risk_checks?.entries_allowed)}</p>
            {(d.risk_checks?.blockers ?? []).length > 0 && <ul className="list-disc pl-5 text-xs text-down">{d.risk_checks.blockers.map((b: string) => <li key={b}>Blocker: {b}</li>)}</ul>}
            {(d.risk_checks?.conflicts ?? []).length > 0 && <ul className="list-disc pl-5 text-xs text-warn">{d.risk_checks.conflicts.map((c: string) => <li key={c}>Conflict: {c}</li>)}</ul>}
          </div>
          <div>
            <p className="text-xs font-medium text-muted">Strategies ({eligible.length} of {strategies.length} eligible; setups on the bar: {(d.strategy_signals?.setups_now ?? []).join(", ") || "none"})</p>
            <table className="mt-1 w-full text-xs">
              <thead><tr className="text-left text-muted"><th className="pr-2">Strategy</th><th className="pr-2">Eligible</th><th>First reason it is not</th></tr></thead>
              <tbody>
                {strategies.map(([code, v]) => (
                  <tr key={code} className="border-t border-line align-top"><td className="py-0.5 pr-2">{code}</td><td className="pr-2">{v.eligible ? "yes" : "no"}</td><td>{v.why_not ?? DASH}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
          {d.reasons?.length > 0 && (
            <div><p className="text-xs font-medium text-muted">Reasons</p><ul className="list-disc pl-5 text-xs">{d.reasons.map((r: string) => <li key={r}>{r}</li>)}</ul></div>
          )}
        </div>
      </details>
    </Card>
  );
}

export default function Decisions() {
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>("all");
  const query = filter === "all" ? "" : `&decision=${filter}`;
  const { data, error, loading } = useApi<Any>(`/decision/log?limit=100${query}`, 60000);
  return (
    <>
      <PageHeader title="Decision log" subtitle="Every recorded decision with the market data, inputs, models, strategy verdicts and risk checks behind it. Recorded after each closed 15-minute bar when something changed, and at least hourly." />
      <div role="group" aria-label="Filter by decision" className="mb-4 flex flex-wrap gap-2 text-xs">
        {FILTERS.map((f) => (
          <button key={f} type="button" aria-pressed={filter === f} onClick={() => setFilter(f)}
            className={`rounded-md border border-line px-3 py-1 ${filter === f ? "bg-elevated font-medium text-text" : "text-muted"}`}>
            {f === "all" ? "All" : LABEL[f]}
          </button>
        ))}
      </div>
      <Loading error={error} loading={loading && !data} />
      {data && (
        <div className="space-y-4">
          <p className="text-sm text-muted">{data.note}</p>
          {data.decisions.length === 0 && <Card><p className="text-sm text-muted">No decisions recorded yet. The first is written after the next closed 15-minute bar.</p></Card>}
          {data.decisions.map((d: Any) => <Decision key={d.id} d={d} />)}
        </div>
      )}
    </>
  );
}
