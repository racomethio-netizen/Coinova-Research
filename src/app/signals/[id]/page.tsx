import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { z } from "zod";
import { CandleChart, type ChartLevel } from "@/components/market/candle-chart";
import { ReportButton } from "@/components/report-button";
import { DirectionBadge, OutcomeBadge, Price, RiskDisclosure } from "@/components/nexus/bits";
import { Badge, Card, CardBody, CardHeader, Container, Notice, PageHeader, Stat } from "@/components/ui";
import { getViewer } from "@/lib/auth/session";
import { fmtDate } from "@/lib/format";
import { getCandles, getSignal } from "@/server/nexus";

export const metadata: Metadata = { title: "Signal" };
export const dynamic = "force-dynamic";

const EVENT_LABEL: Record<string, string> = {
  entered: "Entered", tp1_hit: "Target 1 hit", sl_hit: "Stop hit", expired_no_entry: "Expired without entry",
  invalidated: "Invalidated (price gapped through the stop before entry)", horizon_reached: "Closed at the horizon",
};

export default async function SignalPage({ params }: { params: Promise<{ id: string }> }) {
  const id = z.uuid().safeParse((await params).id);
  if (!id.success) notFound();
  const viewer = await getViewer();
  if (!viewer.supabase) notFound();
  const s = await getSignal(viewer.supabase, id.data);
  if (!s) notFound(); // also when the gate or plan does not allow viewing it (RLS)
  const candles = await getCandles(viewer.supabase, s.pairId, s.timeframe, 300);
  const levels: ChartLevel[] = [
    { price: Number(s.entryLow), label: "Entry low", tone: "entry" },
    { price: Number(s.entryHigh), label: "Entry high", tone: "entry" },
    { price: Number(s.stopLoss), label: "Stop", tone: "stop" },
    ...s.takeProfits.map((tp, i) => ({ price: Number(tp), label: `Target ${i + 1}`, tone: "target" as const })),
  ];
  const ex = s.explanation as { summary?: string; reasons?: { code: string; detail: string }[]; risks?: string[]; invalidation?: string; confidence?: string };
  return (
    <Container className="py-8">
      <PageHeader
        eyebrow={<Link href="/signals" className="text-accent">← All signals</Link>}
        title={<span className="flex flex-wrap items-center gap-3"><DirectionBadge direction={s.direction} /> {s.symbol} · {s.timeframe}</span>}
        description={ex.summary}
      />
      {s.visibility !== "public" ? <Notice tone="warning" title={`Visibility: ${s.visibility}`}>This signal was published in {s.visibility === "staff" ? "shadow mode (staff only)" : "the beta"}.</Notice> : null}
      <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Card><CardBody><dl><Stat label="Entry range" value={<><Price v={s.entryLow} /> – <Price v={s.entryHigh} /></>} /></dl></CardBody></Card>
        <Card><CardBody><dl><Stat label="Stop loss" value={<Price v={s.stopLoss} />} /></dl></CardBody></Card>
        <Card><CardBody><dl><Stat label="Targets" value={<>{s.takeProfits.map((t, i) => <span key={t}>{i ? " · " : ""}<Price v={t} /></span>)}</>} sub="The outcome is decided at Target 1" /></dl></CardBody></Card>
        <Card><CardBody><dl><Stat label="Net reward : risk" value={s.riskReward} sub={`Confidence: ${ex.confidence ?? s.confidenceBand}`} /></dl></CardBody></Card>
      </div>
      <Card className="mt-4">
        <CardBody><CandleChart candles={candles} levels={levels} label={`${s.symbol} ${s.timeframe} chart with signal levels`} /></CardBody>
      </Card>
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title="Why this signal" description="Exact values the strategy used at the decision close." />
          <CardBody>
            <ul className="space-y-2 text-sm">{(ex.reasons ?? []).map((r) => <li key={r.code}><span className="font-mono text-xs text-faint">{r.code}</span><br />{r.detail}</li>)}</ul>
            {ex.invalidation ? <p className="mt-3 text-sm"><span className="font-medium">Invalidation:</span> {ex.invalidation}</p> : null}
            <ul className="mt-3 list-disc space-y-1 pl-5 text-xs text-muted">{(ex.risks ?? []).map((r) => <li key={r}>{r}</li>)}</ul>
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Lifecycle" action={<OutcomeBadge outcome={s.outcome?.outcome ?? null} />} />
          <CardBody className="space-y-3 text-sm">
            <p className="text-muted">Decided at the close of {fmtDate(s.asOf, { time: true })}; published {fmtDate(s.publishedAt, { time: true })}. Entry window ends {fmtDate(s.entryExpiresAt, { time: true })}; horizon {s.horizonBars} bars.</p>
            <ol className="space-y-1">
              {s.events.length ? s.events.map((e) => <li key={e.event_type}>{fmtDate(e.occurred_at, { time: true })} — {EVENT_LABEL[e.event_type] ?? e.event_type}{e.price ? <> at <Price v={e.price} /></> : null}</li>) : <li className="text-muted">No events yet.</li>}
            </ol>
            {s.outcome ? (
              <p>Net return after fees and slippage: <span className="tabular-nums">{s.outcome.netReturn ? `${(Number(s.outcome.netReturn) * 100).toFixed(2)}%` : "—"}</span> · R multiple: <span className="tabular-nums">{s.outcome.rMultiple ?? "—"}</span></p>
            ) : null}
            <p className="text-xs text-faint">Assumptions: fee {s.assumptions.fee_rate} and slippage {s.assumptions.slippage_rate} per side; resolver {s.assumptions.resolver_version}. Only candles that opened after publication are used.</p>
          </CardBody>
        </Card>
      </div>
      {s.annotations.length ? (
        <Card className="mt-4">
          <CardHeader title="Annotations" description="Corrections and notes added after publication. The signal itself is never edited." />
          <CardBody>
            <ul className="space-y-2 text-sm">{s.annotations.map((a) => <li key={a.created_at}><Badge tone={a.kind === "retraction" ? "negative" : a.kind === "correction" ? "warning" : "neutral"}>{a.kind}</Badge> <span className="text-xs text-faint">{fmtDate(a.created_at, { time: true })}</span><p className="mt-1">{a.body}</p></li>)}</ul>
          </CardBody>
        </Card>
      ) : null}
      <p className="mt-4 break-all font-mono text-xs text-faint">
        Strategy {s.strategy?.key} {s.strategy?.version} · content hash {s.contentHash} · previous {s.prevHash ?? "none (first signal)"}
      </p>
      {viewer.user ? <div className="mt-3"><ReportButton targetType="signal" targetId={s.id} /></div> : null}
      <RiskDisclosure />
    </Container>
  );
}
