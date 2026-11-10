import Link from "next/link";
import { DirectionBadge, OutcomeBadge, Price } from "@/components/nexus/bits";
import { Badge, Card, CardBody, Notice, PageHeader } from "@/components/ui";
import { requireStaffPage } from "@/lib/auth/session";
import { fmtDate } from "@/lib/format";
import { listSignals } from "@/server/nexus";
import { AnnotateForm } from "../nexus-client";

export default async function AdminSignalsPage() {
  const viewer = await requireStaffPage();
  const signals = await listSignals(viewer.supabase, 100);
  return (
    <div className="space-y-6">
      <PageHeader title="Signals" description="All signals, including shadow (staff) ones. Signals cannot be edited or deleted; add a public annotation instead." />
      <Notice>Retractions and corrections appear on the public signal page next to the original. Every annotation is recorded in the audit log.</Notice>
      {signals.length === 0 ? <p className="text-sm text-muted">No signals yet.</p> : signals.map((s) => (
        <Card key={s.id}>
          <CardBody className="space-y-3">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <DirectionBadge direction={s.direction} /> <Link className="font-medium hover:text-accent" href={`/signals/${s.id}`}>{s.symbol} · {s.timeframe}</Link>
              <span>entry <Price v={s.entryLow} />–<Price v={s.entryHigh} />, stop <Price v={s.stopLoss} /></span>
              <OutcomeBadge outcome={s.outcome?.outcome ?? null} /> <Badge tone="warning">{s.visibility}</Badge>
              <span className="text-xs text-faint">{fmtDate(s.publishedAt, { time: true })}</span>
            </div>
            {viewer.isAdmin ? <AnnotateForm signalId={s.id} /> : null}
          </CardBody>
        </Card>
      ))}
    </div>
  );
}
