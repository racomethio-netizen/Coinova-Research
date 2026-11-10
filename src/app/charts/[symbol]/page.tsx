import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { CandleChart, type ChartMarker } from "@/components/market/candle-chart";
import { NotConfigured, RiskDisclosure } from "@/components/nexus/bits";
import { Card, CardBody, Container, PageHeader } from "@/components/ui";
import { getViewer } from "@/lib/auth/session";
import { getCandles, listPairs, listSignals, TIMEFRAMES, TimeframeSchema } from "@/server/nexus";

export const dynamic = "force-dynamic";

export async function generateMetadata({ params }: { params: Promise<{ symbol: string }> }): Promise<Metadata> {
  const { symbol } = await params;
  return { title: `${symbol.toUpperCase().slice(0, 30)} chart` };
}

export default async function ChartPage({ params, searchParams }: { params: Promise<{ symbol: string }>; searchParams: Promise<{ tf?: string }> }) {
  const { symbol } = await params;
  const tf = TimeframeSchema.catch("1h").parse((await searchParams).tf);
  const { supabase } = await getViewer();
  if (!supabase) {
    return (
      <Container className="py-8">
        <PageHeader title={`${symbol.toUpperCase()} chart`} />
        <NotConfigured what="The chart" />
      </Container>
    );
  }
  const pair = (await listPairs(supabase)).find((p) => p.symbol === symbol.toUpperCase());
  if (!pair) notFound();
  const [candles, signals] = await Promise.all([getCandles(supabase, pair.id, tf, 500), listSignals(supabase, 200)]);
  const markers: ChartMarker[] = signals
    .filter((s) => s.pairId === pair.id && s.timeframe === tf)
    .map((s) => ({ t: Date.parse(s.asOf) - TF_MS[tf], direction: s.direction, label: s.direction }));
  return (
    <Container className="py-8">
      <PageHeader title={`${pair.baseAsset}/${pair.quoteAsset}`} description={`Exchange OHLCV candles with EMA 20/50/200 and volume. Source: ${pair.source}.`} />
      <nav aria-label="Timeframe" className="mb-3 flex gap-1">
        {TIMEFRAMES.filter((t) => pair.timeframes.includes(t)).map((t) => (
          <Link key={t} href={`/charts/${pair.symbol}?tf=${t}`} aria-current={t === tf ? "page" : undefined}
            className="rounded-md px-2.5 py-1 text-xs font-medium text-muted hover:bg-subtle hover:text-fg aria-[current=page]:bg-accent-soft aria-[current=page]:text-accent">
            {t}
          </Link>
        ))}
      </nav>
      <Card>
        <CardBody>
          <CandleChart candles={candles} markers={markers} label={`${pair.symbol} ${tf} candlestick chart`} />
          <p className="mt-3 text-xs text-faint">
            Markers show signals you are allowed to see, on the candle each decision used. EMA lines are display-only and match the engine&apos;s definition.
          </p>
        </CardBody>
      </Card>
      <RiskDisclosure />
    </Container>
  );
}

const TF_MS: Record<string, number> = { "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000 };
