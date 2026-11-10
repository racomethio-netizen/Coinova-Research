"use client";

import { useId, useState } from "react";
import { Button, Field, inputClass, Notice } from "@/components/ui";
import { useMutation } from "@/lib/client/use-mutation";

export function BacktestForm({ pairs, defaultStart, defaultEnd }: { pairs: { id: number; symbol: string; timeframes: string[] }[]; defaultStart: string; defaultEnd: string }) {
  const m = useMutation();
  const uid = useId();
  const [v, setV] = useState({ pairId: pairs[0]?.id ?? 0, timeframe: "1h", start: defaultStart, end: defaultEnd, minNetRr: "", horizonBars: "" });
  const set = (k: keyof typeof v) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setV({ ...v, [k]: k === "pairId" ? Number(e.target.value) : e.target.value });
  const tfs = pairs.find((p) => p.id === v.pairId)?.timeframes ?? [];
  return (
    <form
      className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3"
      onSubmit={(e) => {
        e.preventDefault();
        const params: Record<string, number> = {};
        if (v.minNetRr.trim()) params.min_net_rr = Number(v.minNetRr);
        if (v.horizonBars.trim()) params.horizon_bars = Number(v.horizonBars);
        void m.run("/api/backtests", "POST", {
          pairId: v.pairId, timeframe: v.timeframe, start: `${v.start}T00:00:00Z`,
          end: `${v.end}T00:00:00Z`, params,
        });
      }}
    >
      {m.error ? <div className="sm:col-span-2 lg:col-span-3"><Notice tone="negative">{m.error}</Notice></div> : null}
      <Field id={`${uid}-pair`} label="Pair"><select id={`${uid}-pair`} value={v.pairId} onChange={set("pairId")} className={inputClass}>{pairs.map((p) => <option key={p.id} value={p.id}>{p.symbol}</option>)}</select></Field>
      <Field id={`${uid}-tf`} label="Timeframe"><select id={`${uid}-tf`} value={v.timeframe} onChange={set("timeframe")} className={inputClass}>{tfs.map((t) => <option key={t}>{t}</option>)}</select></Field>
      <Field id={`${uid}-start`} label="Start (UTC)" error={m.fields.start}><input id={`${uid}-start`} type="date" value={v.start} onChange={set("start")} className={inputClass} /></Field>
      <Field id={`${uid}-end`} label="End (UTC)" error={m.fields.end}><input id={`${uid}-end`} type="date" value={v.end} onChange={set("end")} className={inputClass} /></Field>
      <Field id={`${uid}-rr`} label="Min net reward:risk (1–5, optional)" error={m.fields["params.min_net_rr"]}><input id={`${uid}-rr`} value={v.minNetRr} onChange={set("minNetRr")} inputMode="decimal" className={inputClass} /></Field>
      <Field id={`${uid}-h`} label="Horizon bars (4–200, optional)" error={m.fields["params.horizon_bars"]}><input id={`${uid}-h`} value={v.horizonBars} onChange={set("horizonBars")} inputMode="numeric" className={inputClass} /></Field>
      <div className="flex items-end"><Button type="submit" disabled={m.pending || !pairs.length}>{m.pending ? "Queuing…" : "Run backtest"}</Button></div>
    </form>
  );
}
