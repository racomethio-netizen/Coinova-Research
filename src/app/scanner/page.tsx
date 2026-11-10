import type { Metadata } from "next";
import Link from "next/link";
import { NotConfigured, Price, RiskDisclosure } from "@/components/nexus/bits";
import { Badge, Card, Container, EmptyState, PageHeader } from "@/components/ui";
import { getViewer } from "@/lib/auth/session";
import { fmtDate } from "@/lib/format";
import { getScanner, listPairs } from "@/server/nexus";

export const metadata: Metadata = { title: "Market scanner", description: "Indicator states for each pair and timeframe, from exchange OHLCV candles." };
export const dynamic = "force-dynamic";

function trend(close: number, ema200: number) {
  return close > ema200 ? { label: "Above EMA200", tone: "positive" as const } : { label: "Below EMA200", tone: "negative" as const };
}

function rsiLabel(rsi: number) {
  return rsi >= 70 ? "Overbought (≥70)" : rsi <= 30 ? "Oversold (≤30)" : "Neutral";
}

export default async function ScannerPage() {
  const { supabase } = await getViewer();
  const [rows, pairs] = supabase ? await Promise.all([getScanner(supabase), listPairs(supabase)]).catch(() => [null, null] as const) : [null, null];
  return (
    <Container className="py-8">
      <PageHeader
        title="Market scanner"
        description="Latest closed-candle indicator states per pair and timeframe. Values are the engine's own calculations, not forecasts."
      />
      {!supabase || rows === null ? (
        <NotConfigured what="The scanner" />
      ) : rows.length === 0 ? (
        <EmptyState title="No indicator data yet" description="The engine has not evaluated any closed candles yet. Indicators appear after market data is ingested." />
      ) : (
        <Card className="overflow-x-auto">
          <table className="w-full min-w-[720px] text-sm">
            <caption className="sr-only">Indicator states by pair and timeframe</caption>
            <thead className="text-left text-xs uppercase tracking-wide text-faint">
              <tr>
                {["Pair", "TF", "Close", "Trend", "Higher TF", "RSI(14)", "MACD hist", "ATR(14)", "Vol ×avg", "As of"].map((h) => (
                  <th key={h} scope="col" className="px-3 py-2.5">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {rows.map((r) => {
                const t = trend(Number(r.close), Number(r.ema200));
                const htfUp = Number(r.htf_ema50) > Number(r.htf_ema200);
                return (
                  <tr key={`${r.pair_id}-${r.timeframe}`}>
                    <th scope="row" className="px-3 py-2 text-left font-medium">
                      <Link className="hover:text-accent" href={`/charts/${r.symbol}?tf=${r.timeframe}`}>{r.symbol}</Link>
                    </th>
                    <td className="px-3 py-2">{r.timeframe}</td>
                    <td className="px-3 py-2"><Price v={r.close} /></td>
                    <td className="px-3 py-2"><Badge tone={t.tone}>{t.label}</Badge></td>
                    <td className="px-3 py-2"><Badge tone={htfUp ? "positive" : "negative"}>{r.htf} {htfUp ? "EMA50 > EMA200" : "EMA50 < EMA200"}</Badge></td>
                    <td className="px-3 py-2 tabular-nums">{Number(r.rsi14).toFixed(1)} <span className="text-xs text-faint">{rsiLabel(Number(r.rsi14))}</span></td>
                    <td className="px-3 py-2 tabular-nums">{Number(r.macd_hist).toPrecision(4)}</td>
                    <td className="px-3 py-2"><Price v={r.atr14} /></td>
                    <td className="px-3 py-2 tabular-nums">{Number(r.volume_ratio).toFixed(2)}</td>
                    <td className="px-3 py-2 text-xs text-muted">{fmtDate(r.as_of, { time: true })}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Card>
      )}
      {pairs && pairs.length ? (
        <p className="mt-3 text-xs text-faint">Source: {Array.from(new Set(pairs.map((p) => p.source))).join(", ")}. Times are UTC candle closes.</p>
      ) : null}
      <RiskDisclosure />
    </Container>
  );
}
