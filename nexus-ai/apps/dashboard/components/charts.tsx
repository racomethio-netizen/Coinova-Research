"use client";

import { useEffect, useRef } from "react";
import { AreaSeries, CandlestickSeries, ColorType, createChart, type IChartApi, type UTCTimestamp } from "lightweight-charts";

const THEME = {
  layout: { background: { type: ColorType.Solid, color: "#11161e" }, textColor: "#8a96a8" },
  grid: { vertLines: { color: "#1b2330" }, horzLines: { color: "#1b2330" } },
  rightPriceScale: { borderColor: "#232c3a" },
  timeScale: { borderColor: "#232c3a", timeVisible: true },
};

export type CandleRow = { t: number; o: string; h: string; l: string; c: string };

function useChart(build: (chart: IChartApi) => void, deps: unknown[]) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const chart = createChart(ref.current, { ...THEME, autoSize: true });
    build(chart);
    chart.timeScale().fitContent();
    return () => chart.remove();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return ref;
}

export function CandleChart({ candles, label }: { candles: CandleRow[]; label: string }) {
  const ref = useChart((chart) => {
    const s = chart.addSeries(CandlestickSeries, {
      upColor: "#22c55e", downColor: "#ef4444", wickUpColor: "#22c55e", wickDownColor: "#ef4444", borderVisible: false,
    });
    s.setData(candles.map((c) => ({ time: c.t as UTCTimestamp, open: Number(c.o), high: Number(c.h), low: Number(c.l), close: Number(c.c) })));
  }, [candles]);
  return <div ref={ref} role="img" aria-label={label} className="h-96 w-full" />;
}

export function EquityChart({ points, label }: { points: { t: string; equity: string }[]; label: string }) {
  const ref = useChart((chart) => {
    const s = chart.addSeries(AreaSeries, { lineColor: "#4f8cff", topColor: "rgba(79,140,255,0.3)", bottomColor: "rgba(79,140,255,0.02)" });
    const seen = new Set<number>();
    s.setData(points
      .map((p) => ({ time: Math.floor(new Date(p.t).getTime() / 1000) as UTCTimestamp, value: Number(p.equity) }))
      .filter((p) => (seen.has(p.time) ? false : (seen.add(p.time), true))));
  }, [points]);
  return <div ref={ref} role="img" aria-label={label} className="h-72 w-full" />;
}
