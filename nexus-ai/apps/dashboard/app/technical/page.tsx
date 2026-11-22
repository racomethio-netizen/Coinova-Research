"use client";

import { useState } from "react";
import { Badge, Card, Loading, PageHeader, Stat } from "@/components/ui";
import { DASH, time } from "@/lib/format";
import { useApi } from "@/lib/hooks";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

const SEL = "rounded-lg border border-line bg-elevated px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-accent";
const TFS = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"];
const GROUPS: [string, string][] = [["trend", "Trend"], ["momentum", "Momentum"], ["volatility", "Volatility"], ["volume", "Volume"], ["structure", "Price structure"], ["liquidity", "Liquidity"], ["candle", "Last candle"]];

function fmt(v: Any): string {
  if (v === null || v === undefined) return DASH;
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (typeof v === "number") return Math.abs(v) >= 1000 ? v.toLocaleString(undefined, { maximumFractionDigits: 2 }) : String(v);
  if (typeof v === "string" && /^\d{4}-\d{2}-\d{2}T/.test(v)) return time(v);
  return String(v);
}

function lean(v: number | null | undefined): { label: string; tone: "good" | "bad" | "muted" } {
  if (v === null || v === undefined) return { label: "no data", tone: "muted" };
  if (v > 0.3) return { label: `up ${v.toFixed(2)}`, tone: "good" };
  if (v < -0.3) return { label: `down ${v.toFixed(2)}`, tone: "bad" };
  return { label: `neutral ${v.toFixed(2)}`, tone: "muted" };
}

function Group({ title, data }: { title: string; data: Record<string, Any> }) {
  return (
    <Card title={title}>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-sm">
        {Object.entries(data).filter(([, v]) => typeof v !== "object" || v === null).map(([k, v]) => (
          <div key={k} className="contents"><dt className="text-muted">{k.replaceAll("_", " ")}</dt><dd className="num">{fmt(v)}</dd></div>
        ))}
      </dl>
    </Card>
  );
}

export default function Technical() {
  const [symbol, setSymbol] = useState("BTC/USDT");
  const [tf, setTf] = useState("1h");
  const [base, setBase] = useState("5m");
  const tech = useApi<Any>(`/analysis/technical?symbol=${encodeURIComponent(symbol)}&timeframe=${tf}`, 60000);
  const mtf = useApi<Any>(`/analysis/mtf?symbol=${encodeURIComponent(symbol)}&base=${base}`, 60000);
  const t = tech.data;
  const m = mtf.data;
  return (
    <>
      <PageHeader title="Technical analysis" subtitle="Indicators and multi-timeframe states from stored closed candles. Analysis only: these values never place a trade on their own.">
        <div className="flex flex-wrap gap-2">
          <label className="sr-only" htmlFor="ta-sym">Pair</label>
          <select id="ta-sym" className={SEL} value={symbol} onChange={(e) => setSymbol(e.target.value)}>
            {["BTC/USDT", "ETH/USDT"].map((x) => <option key={x}>{x}</option>)}
          </select>
          <label className="sr-only" htmlFor="ta-tf">Indicator timeframe</label>
          <select id="ta-tf" className={SEL} value={tf} onChange={(e) => setTf(e.target.value)}>
            {TFS.map((x) => <option key={x}>{x}</option>)}
          </select>
        </div>
      </PageHeader>
      <div className="space-y-4">
        <Card title="Multi-timeframe view" actions={
          <><label className="sr-only" htmlFor="ta-base">Base timeframe</label>
          <select id="ta-base" className={SEL} value={base} onChange={(e) => setBase(e.target.value)}>
            {["1m", "3m", "5m", "15m", "1h", "4h"].map((x) => <option key={x}>{x}</option>)}
          </select></>}>
          <Loading error={mtf.error} loading={mtf.loading && !m} />
          {m && (
            <>
              <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                <Stat label="Trend agreement" value={lean(m.scores?.trend_agreement).label} />
                <Stat label="Momentum agreement" value={lean(m.scores?.momentum_agreement).label} />
                <Stat label="Higher-timeframe trend" value={lean(m.scores?.htf_trend).label} />
                <Stat label="Conflict" value={m.scores?.conflict == null ? DASH : m.scores.conflict ? "yes: lower and higher disagree" : "no"} tone={m.scores?.conflict ? "warn" : undefined} />
              </div>
              <div className="mt-3 overflow-x-auto">
                <table className="w-full text-sm">
                  <thead><tr className="text-left text-muted"><th className="py-1 pr-3">TF</th><th className="pr-3">Trend</th><th className="pr-3">Momentum</th><th className="pr-3">Volatility</th><th className="pr-3">Structure</th><th className="pr-3">RSI</th><th className="pr-3">ADX</th><th>Last bar</th></tr></thead>
                  <tbody>
                    {Object.entries(m.timeframes as Record<string, Any>).map(([k, s]) => (
                      <tr key={k} className="border-t border-line">
                        <td className="py-1 pr-3 font-medium">{k}</td>
                        {(["trend", "momentum", "volatility", "structure"] as const).map((c) => {
                          const l = lean(s[c]);
                          return <td key={c} className="pr-3"><Badge tone={l.tone}>{c === "volatility" && s[c] != null ? (s[c] > 0 ? "expanding" : s[c] < 0 ? "contracting" : "normal") : l.label}</Badge></td>;
                        })}
                        <td className="num pr-3">{fmt(s.rsi)}</td><td className="num pr-3">{fmt(s.adx)}</td><td className="text-xs text-muted">{fmt(s.last_bar)} ({s.bars} bars)</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="mt-2 text-xs text-muted">Base {m.base_timeframe} bar closed {fmt(m.as_of)}. Each timeframe uses only bars closed by then.{m.missing_timeframes?.length ? ` No stored data yet for: ${m.missing_timeframes.join(", ")}.` : ""} {m.note}</p>
            </>
          )}
        </Card>
        <Loading error={tech.error} loading={tech.loading && !t} />
        {t && t.insufficient_history && <p className="text-sm text-muted">Not enough stored {tf} candles yet ({t.bars ?? 0}); indicators are not shown rather than estimated.</p>}
        {t && !t.insufficient_history && (
          <>
            <p className="text-sm text-muted">{symbol} {tf}: last closed bar opened {fmt(t.as_of_open)}, close {fmt(t.close)}, {t.bars} bars.</p>
            <div className="grid gap-4 md:grid-cols-2">
              {GROUPS.map(([k, label]) => t[k] ? <Group key={k} title={label} data={t[k]} /> : null)}
            </div>
            <Card title="Limitations">
              <ul className="list-disc pl-5 text-sm text-muted">{(t.limitations as string[]).map((x) => <li key={x}>{x}</li>)}</ul>
            </Card>
          </>
        )}
      </div>
    </>
  );
}
