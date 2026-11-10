"use client";

import {
  CandlestickSeries, ColorType, createChart, createSeriesMarkers, HistogramSeries, LineSeries, LineStyle,
  type IChartApi, type SeriesMarker, type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef } from "react";
import { ema } from "@/lib/market/ta";

export interface Candle {
  t: number;
  o: number;
  h: number;
  l: number;
  c: number;
  v: number;
}

export interface ChartMarker {
  t: number;
  direction: "BUY" | "SELL";
  label: string;
}

export interface ChartLevel {
  price: number;
  label: string;
  tone: "entry" | "stop" | "target";
}

function cssVar(name: string) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

const ts = (ms: number) => Math.floor(ms / 1000) as UTCTimestamp;

/**
 * Candlesticks with volume, EMA 20/50/200 overlays, signal markers and level lines.
 * Markers carry text (BUY/SELL), so direction is never conveyed by colour alone.
 */
export function CandleChart({ candles, markers = [], levels = [], label }: { candles: Candle[]; markers?: ChartMarker[]; levels?: ChartLevel[]; label: string }) {
  const container = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (!container.current || candles.length === 0) return;
    const chart = createChart(container.current, {
      autoSize: true,
      localization: { locale: "en-US" },
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: cssVar("--fg-muted"), attributionLogo: true },
      grid: { vertLines: { color: cssVar("--border") }, horzLines: { color: cssVar("--border") } },
      rightPriceScale: { borderColor: cssVar("--border") },
      timeScale: { borderColor: cssVar("--border"), timeVisible: true },
      crosshair: { mode: 1 },
    });
    chartRef.current = chart;
    const up = cssVar("--positive");
    const down = cssVar("--negative");
    const series = chart.addSeries(CandlestickSeries, { upColor: up, downColor: down, wickUpColor: up, wickDownColor: down, borderVisible: false });
    series.setData(candles.map((c) => ({ time: ts(c.t), open: c.o, high: c.h, low: c.l, close: c.c })));
    const volume = chart.addSeries(HistogramSeries, { priceScaleId: "volume", priceFormat: { type: "volume" }, color: cssVar("--border-strong") });
    chart.priceScale("volume").applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    volume.setData(candles.map((c) => ({ time: ts(c.t), value: c.v })));
    const closes = candles.map((c) => c.c);
    const colors: Record<number, string> = { 20: cssVar("--accent"), 50: cssVar("--warning"), 200: cssVar("--fg-subtle") };
    for (const n of [20, 50, 200]) {
      const line = chart.addSeries(LineSeries, { color: colors[n], lineWidth: 1, priceLineVisible: false, lastValueVisible: false, title: `EMA${n}` });
      line.setData(ema(closes, n).flatMap((v, i) => (v === null ? [] : [{ time: ts(candles[i]!.t), value: v }])));
    }
    if (markers.length) {
      const m: SeriesMarker<UTCTimestamp>[] = markers
        .filter((x) => candles.some((c) => c.t === x.t))
        .sort((a, b) => a.t - b.t)
        .map((x) => ({
          time: ts(x.t), position: x.direction === "BUY" ? "belowBar" : "aboveBar", shape: x.direction === "BUY" ? "arrowUp" : "arrowDown",
          color: x.direction === "BUY" ? up : down, text: x.label,
        }));
      createSeriesMarkers(series, m);
    }
    const toneColor = { entry: cssVar("--accent"), stop: down, target: up };
    for (const lv of levels) {
      series.createPriceLine({ price: lv.price, color: toneColor[lv.tone], lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: lv.label });
    }
    chart.timeScale().fitContent();
    return () => {
      chart.remove();
      chartRef.current = null;
    };
  }, [candles, markers, levels]);

  return (
    <div className="relative h-80 w-full sm:h-96">
      <div ref={container} className="absolute inset-0" role="img" aria-label={label} />
      {candles.length === 0 ? (
        <div className="absolute inset-0 flex items-center justify-center text-sm text-muted">No candles stored for this pair and timeframe yet.</div>
      ) : null}
    </div>
  );
}
