import type { Metadata } from "next";
import Link from "next/link";
import { DirectionBadge, NotConfigured, OutcomeBadge, Price, RiskDisclosure } from "@/components/nexus/bits";
import { Badge, Card, Container, EmptyState, Notice, PageHeader } from "@/components/ui";
import { getViewer, hasFeature } from "@/lib/auth/session";
import { getNexusGates } from "@/lib/env";
import { fmtDate } from "@/lib/format";
import { listSignals } from "@/server/nexus";

export const metadata: Metadata = { title: "Signals", description: "Published research signals with their full, unedited track record." };
export const dynamic = "force-dynamic";

export default async function SignalsPage() {
  const viewer = await getViewer();
  const gates = getNexusGates();
  const signals = viewer.supabase ? await listSignals(viewer.supabase).catch(() => null) : null;
  return (
    <Container className="py-8">
      <PageHeader title="Signals" description="Every published signal stays here, wins and losses alike. Published signals are never edited or deleted; corrections are added as annotations." />
      {gates.signalsVisibility === "staff" && !viewer.isStaff ? (
        <Notice tone="accent" title="Signals are in internal review">
          The signal engine is running in shadow mode while its methodology is validated and reviewed. Signals will appear here once publication is approved.
        </Notice>
      ) : null}
      {gates.signalsVisibility === "public" && !hasFeature(viewer, "signals.history") ? (
        <Notice tone="neutral" title="Showing closed signals older than 7 days">
          Pro and Premium members see live signals and the full history. <Link className="text-accent underline" href="/pricing">Compare plans</Link>.
        </Notice>
      ) : null}
      {!viewer.supabase || signals === null ? (
        <div className="mt-4"><NotConfigured what="Signals" /></div>
      ) : signals.length === 0 ? (
        <div className="mt-4"><EmptyState title="No signals to show" description="Nothing has been published that you can view yet." /></div>
      ) : (
        <Card className="mt-4 overflow-x-auto">
          <table className="w-full min-w-[760px] text-sm">
            <caption className="sr-only">Published signals</caption>
            <thead className="text-left text-xs uppercase tracking-wide text-faint">
              <tr>{["Signal", "Pair", "TF", "Entry", "Stop", "Target 1", "Net R:R", "Status", "Published"].map((h) => <th key={h} scope="col" className="px-3 py-2.5">{h}</th>)}</tr>
            </thead>
            <tbody className="divide-y divide-line">
              {signals.map((s) => (
                <tr key={s.id}>
                  <th scope="row" className="px-3 py-2 text-left"><Link href={`/signals/${s.id}`} className="hover:opacity-80"><DirectionBadge direction={s.direction} /></Link></th>
                  <td className="px-3 py-2 font-medium">{s.symbol}</td>
                  <td className="px-3 py-2">{s.timeframe}</td>
                  <td className="px-3 py-2"><Price v={s.entryLow} />–<Price v={s.entryHigh} /></td>
                  <td className="px-3 py-2"><Price v={s.stopLoss} /></td>
                  <td className="px-3 py-2"><Price v={s.takeProfits[0]} /></td>
                  <td className="px-3 py-2 tabular-nums">{s.riskReward}</td>
                  <td className="px-3 py-2"><OutcomeBadge outcome={s.outcome?.outcome ?? null} />{s.visibility !== "public" ? <Badge className="ml-1" tone="warning">{s.visibility}</Badge> : null}</td>
                  <td className="px-3 py-2 text-xs text-muted">{fmtDate(s.publishedAt, { time: true })}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
      <RiskDisclosure />
    </Container>
  );
}
