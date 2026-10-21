"use client";

import { AreaSeries, ColorType, createChart, type IChartApi, type UTCTimestamp } from "lightweight-charts";
import { useEffect, useRef, useState } from "react";

export interface ChartPoint {
  t: number;
  v: number;
}

const PERIODS = [
  { id: "1d", label: "24H" },
  { id: "7d", label: "7D" },
  { id: "30d", label: "30D" },
  { id: "90d", label: "90D" },
  { id: "365d", label: "1Y" },
] as const;

type Period = (typeof PERIODS)[number]["id"];

interface HistoryResponse {
  ok: boolean;
  data?: { points: { t: number; priceUsd: number }[]; source: string; fetchedAt: string; stale: boolean };
  error?: { message: string };
}

function cssVar(name: string) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export function PriceChart({ coinId, initial, initialPeriod = "30d" }: { coinId: string; initial: ChartPoint[]; initialPeriod?: Period }) {
  const container = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const [period, setPeriod] = useState<Period>(initialPeriod);
  const [points, setPoints] = useState<ChartPoint[]>(initial);
  const [status, setStatus] = useState<"idle" | "loading" | "error">("idle");
  const [meta, setMeta] = useState<{ stale: boolean } | null>(null);

  useEffect(() => {
    if (!container.current) return;
    const chart = createChart(container.current, {
      autoSize: true,
      // Explicit locale: navigator.language can be a tag Intl rejects (e.g. "en-US@posix"), which breaks rendering.
      localization: { locale: "en-US" },
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: cssVar("--fg-muted"), attributionLogo: true },
      grid: { vertLines: { color: cssVar("--border") }, horzLines: { color: cssVar("--border") } },
      rightPriceScale: { borderColor: cssVar("--border") },
      timeScale: { borderColor: cssVar("--border"), timeVisible: true },
      crosshair: { mode: 1 },
    });
    chartRef.current = chart;
    return () => {
      chart.remove();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const accent = cssVar("--accent");
    const series = chart.addSeries(AreaSeries, {
      lineColor: accent,
      topColor: `${accent}55`,
      bottomColor: `${accent}05`,
      lineWidth: 2,
      priceFormat: { type: "price", precision: precisionFor(points), minMove: Math.pow(10, -precisionFor(points)) },
    });
    series.setData(points.map((p) => ({ time: Math.floor(p.t / 1000) as UTCTimestamp, value: p.v })));
    chart.timeScale().fitContent();
    return () => {
      chart.removeSeries(series);
    };
  }, [points]);

  async function load(next: Period) {
    setPeriod(next);
    setStatus("loading");
    try {
      const res = await fetch(`/api/coins/${encodeURIComponent(coinId)}/history?period=${next}`);
      const json = (await res.json()) as HistoryResponse;
      if (!json.ok || !json.data) throw new Error(json.error?.message ?? "Failed to load history");
      setPoints(json.data.points.map((p) => ({ t: p.t, v: p.priceUsd })));
      setMeta({ stale: json.data.stale });
      setStatus("idle");
    } catch {
      setStatus("error");
    }
  }

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-1" role="group" aria-label="Chart period">
        {PERIODS.map((p) => (
          <button
            key={p.id}
            type="button"
            onClick={() => load(p.id)}
            aria-pressed={period === p.id}
            className="rounded-md px-2.5 py-1 text-xs font-medium text-muted hover:bg-subtle hover:text-fg aria-pressed:bg-accent-soft aria-pressed:text-accent"
          >
            {p.label}
          </button>
        ))}
        {status === "loading" ? <span className="ml-2 text-xs text-faint">Loading…</span> : null}
        {status === "error" ? <span className="ml-2 text-xs text-negative" role="alert">Could not load this period. The provider may be unavailable.</span> : null}
        {meta?.stale ? <span className="ml-2 text-xs text-warning">Showing cached (stale) history</span> : null}
      </div>
      <div className="relative h-72 w-full sm:h-80">
        <div ref={container} className="absolute inset-0" aria-label="Historical price chart" role="img" />
        {points.length === 0 ? (
          <div className="absolute inset-0 flex items-center justify-center text-sm text-muted">No historical data available for this period.</div>
        ) : null}
      </div>
    </div>
  );
}

function precisionFor(points: ChartPoint[]) {
  const last = points.at(-1)?.v ?? 1;
  if (last >= 1) return 2;
  if (last >= 0.01) return 4;
  return 8;
}
