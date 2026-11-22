"use client";

import { Badge, Card, Loading, PageHeader, Stat, Table } from "@/components/ui";
import { DASH, num, pct, time } from "@/lib/format";
import { useApi } from "@/lib/hooks";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

const LABELS: Record<string, string> = {
  trend_alignment: "Higher-timeframe trend agrees", structure_event: "Break of structure / change of character",
  level_confluence: "At support or resistance", liquidity_sweep: "Recent liquidity sweep",
  volume_expansion: "Volume expansion", book_imbalance: "Order-book imbalance agrees",
  btc_not_against: "BTC not moving against",
};
const strengthTone = (s: string) => (s === "strong" ? "good" : s === "moderate" ? "info" : s === "weak" ? "warn" : "muted");

function Hypothesis({ h }: { h: Any }) {
  return (
    <div className="rounded-xl border border-line p-4">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="font-semibold">{h.futures_action}</span>
        <span className="text-xs text-muted">(spot: {h.spot_action})</span>
        <Badge tone={strengthTone(h.strength)}>{h.strength}</Badge>
        <span className="num text-sm">score {h.score}/100</span>
        <Badge tone={h.decision === "ENTER" ? "good" : "bad"}>{h.decision === "ENTER" ? "setup qualifies" : "NO TRADE"}</Badge>
      </div>
      <p className="num mb-2 text-xs text-muted">entry {num(h.entry)} · stop {num(h.stop)} · target {num(h.target)} · R:R {num(h.reward_risk, 1)}</p>
      <div className="grid gap-2 text-xs md:grid-cols-2">
        <div><p className="mb-1 font-medium text-up">For</p><ul className="list-disc pl-4">{h.reasons_for.length ? h.reasons_for.map((r: string) => <li key={r}>{LABELS[r] ?? r}</li>) : <li>none</li>}</ul></div>
        <div><p className="mb-1 font-medium text-down">Against</p><ul className="list-disc pl-4">{h.reasons_against.map((r: string) => <li key={r}>{LABELS[r] ?? r}</li>)}</ul></div>
      </div>
      {h.no_trade_reasons.length > 0 && <p className="mt-2 text-xs text-warn">NO TRADE because: {h.no_trade_reasons.join("; ")}</p>}
    </div>
  );
}

export default function IntelligencePage() {
  const r = useApi<Any>("/intelligence", 60_000);
  const d = r.data;
  return (
    <>
      <PageHeader title="AI intelligence" subtitle="Live analysis from Binance public market data (closed candles only). Scores use the same rules as the trading engine. Execution additionally requires a strategy that passed out-of-sample validation; none has." />
      <Loading error={r.error} loading={r.loading && !d} />
      {d && <p className="mb-3 text-xs text-muted">Generated {time(d.generated_at)}{d.cached ? " (cached)" : ""} · minimum score {d.min_score} · source {d.source}</p>}
      <div className="space-y-4">
        {d?.pairs.map((p: Any) => p.error ? (
          <Card key={p.symbol} title={p.symbol}><p role="alert" className="text-sm text-down">{p.error}</p></Card>
        ) : (
          <Card key={p.symbol} title={p.symbol} actions={<Badge tone={p.regime === "high_vol" ? "warn" : "info"}>{p.regime ?? "regime unknown"}</Badge>}>
            <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
              <Stat label="Price" value={num(p.price)} hint={`24h ${pct(p.change_24h)}`} tone={p.change_24h > 0 ? "up" : p.change_24h < 0 ? "down" : "flat"} />
              <Stat label="24h VWAP" value={num(p.vwap_24h)} />
              <Stat label="Support / resistance (1h)" value={`${num(p.support)} / ${num(p.resistance)}`} />
              <Stat label="BTC correlation (1h)" value={p.btc_correlation_1h === null ? DASH : num(p.btc_correlation_1h)} hint={p.btc_trend_1h ? `BTC trend ${p.btc_trend_1h}` : undefined} />
              <Stat label="Book imbalance" value={p.order_book ? num(p.order_book.imbalance) : DASH} hint={p.order_book ? `spread ${num(p.order_book.spread_bps, 3)} bps` : undefined} />
              <Stat label="Volatility rank (ATR)" value={pct(p.atr_percentile, 0)} />
              <Stat label="Liquidity sweep (12h)" value={p.liquidity_sweep_low_12h ? "below lows" : p.liquidity_sweep_high_12h ? "above highs" : "none"} />
              <Stat label="Funding rate (8h)" value={p.funding_rate == null ? DASH : pct(p.funding_rate, 4)} hint={p.basis == null ? undefined : `perp basis ${pct(p.basis, 3)}`} />
              <Stat label="Open interest" value={p.open_interest_value == null ? (p.open_interest == null ? DASH : num(p.open_interest, 0)) : `${num(p.open_interest_value / 1e6, 1)}M USDT`} hint={p.oi_change_24h == null ? undefined : `24h ${pct(p.oi_change_24h, 1)} · 1h ${pct(p.oi_change_1h, 1)}`} />
              <Stat label="Long/short (top traders)" value={p.top_traders_position_ratio == null ? DASH : num(p.top_traders_position_ratio)} hint={p.accounts_long_short_ratio == null ? undefined : `accounts ${num(p.accounts_long_short_ratio)} (weak signal)`} />
              <Stat label="Liquidations 1h (longs / shorts)" value={p.liquidations ? `${num(p.liquidations.longs_liquidated_1h / 1e3, 0)}k / ${num(p.liquidations.shorts_liquidated_1h / 1e3, 0)}k` : DASH} hint={p.liquidations ? "USDT, lower bound" : "feed not available"} />
            </div>
            {p.futures_data_note && <p className="-mt-2 mb-3 text-xs text-muted">Derivatives: {p.futures_data_note}</p>}
            <div className="mb-4 grid gap-3 lg:grid-cols-2">{p.hypotheses.map((h: Any) => <Hypothesis key={h.side} h={h} />)}</div>
            <Table rows={Object.entries(p.timeframes).map(([tf, v]: [string, Any]) => ({ tf, ...v }))} empty="—" columns={[
              { key: "tf", label: "TF" },
              { key: "trend", label: "Trend", render: (v: Any) => <Badge tone={v.trend === "up" ? "good" : v.trend === "down" ? "bad" : "muted"}>{v.trend ?? DASH}</Badge> },
              { key: "structure_event", label: "Structure", render: (v: Any) => v.structure_event ?? DASH },
              { key: "rsi", label: "RSI", numeric: true, render: (v: Any) => num(v.rsi, 1) },
              { key: "macd_hist", label: "MACD hist", numeric: true, render: (v: Any) => num(v.macd_hist, 2) },
              { key: "adx", label: "ADX", numeric: true, render: (v: Any) => num(v.adx, 1) },
              { key: "atr_pct", label: "ATR %", numeric: true, render: (v: Any) => pct(v.atr_pct) },
              { key: "bollinger_pct_b", label: "Bollinger %B", numeric: true, render: (v: Any) => num(v.bollinger_pct_b) },
              { key: "ema", label: "EMA 20/50/200", render: (v: Any) => `${num(v.ema20)} / ${num(v.ema50)} / ${num(v.ema200)}` },
              { key: "volume_z", label: "Vol z", numeric: true, render: (v: Any) => num(v.volume_z, 1) },
            ]} />
            {p.zones.length > 0 && (
              <p className="mt-3 text-xs text-muted">Recent zones (1h): {p.zones.map((z: Any) => `${z.kind} ${num(z.low)}–${num(z.high)}${z.mitigated_at ? " (mitigated)" : ""}`).join(" · ")}</p>
            )}
          </Card>
        ))}
      </div>
    </>
  );
}
