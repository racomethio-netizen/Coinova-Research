"use client";

import { ColorType, createChart, LineSeries, type UTCTimestamp } from "lightweight-charts";
import { useEffect, useRef } from "react";

function cssVar(name: string) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** Simple time series line (equity curves). */
export function LineChart({ points, label, empty }: { points: { t: number; v: number }[]; label: string; empty: string }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current || points.length === 0) return;
    const chart = createChart(ref.current, {
      autoSize: true,
      localization: { locale: "en-US" },
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: cssVar("--fg-muted"), attributionLogo: true },
      grid: { vertLines: { color: cssVar("--border") }, horzLines: { color: cssVar("--border") } },
      timeScale: { borderColor: cssVar("--border"), timeVisible: true },
    });
    const s = chart.addSeries(LineSeries, { color: cssVar("--accent"), lineWidth: 2 });
    const seen = new Set<number>();
    s.setData(
      points
        .map((p) => ({ time: Math.floor(p.t / 1000) as UTCTimestamp, value: p.v }))
        .filter((p) => (seen.has(p.time) ? false : (seen.add(p.time), true)))
        .sort((a, b) => a.time - b.time),
    );
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [points]);
  return (
    <div className="relative h-56 w-full">
      <div ref={ref} className="absolute inset-0" role="img" aria-label={label} />
      {points.length === 0 ? <div className="absolute inset-0 flex items-center justify-center text-sm text-muted">{empty}</div> : null}
    </div>
  );
}
