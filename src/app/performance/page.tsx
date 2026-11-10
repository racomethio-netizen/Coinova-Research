import type { Metadata } from "next";
import { NotConfigured, RiskDisclosure } from "@/components/nexus/bits";
import { Badge, Card, CardBody, CardHeader, Container, EmptyState, Notice, PageHeader } from "@/components/ui";
import { getViewer } from "@/lib/auth/session";
import { fmtDate } from "@/lib/format";
import { getPerformance } from "@/server/nexus";

export const metadata: Metadata = { title: "Performance", description: "Forward (live) signal results computed from the immutable signal ledger." };
export const dynamic = "force-dynamic";

/** Below this many closed, entered signals no rate is headlined (docs/nexus/COMPLIANCE.md §4). */
const MIN_SAMPLE = 100;

export default async function PerformancePage() {
  const viewer = await getViewer();
  const rows = viewer.supabase ? await getPerformance(viewer.supabase).catch(() => null) : null;
  return (
    <Container className="py-8">
      <PageHeader title="Performance" description="Forward results only: every published signal you can see, resolved on candles that opened after publication, after fees and slippage." />
      <Notice tone="neutral" title="How to read this">
        Results are never cherry-picked: losses, expirations and invalidations all count, and published signals cannot be edited or deleted. Below {MIN_SAMPLE} entered trades a group is marked
        <strong> insufficient sample</strong> and no win rate is shown. Backtests are hypothetical and are shown only on your own backtest pages, never mixed with these results.
      </Notice>
      {!viewer.supabase || rows === null ? (
        <div className="mt-4"><NotConfigured what="Performance data" /></div>
      ) : rows.length === 0 ? (
        <div className="mt-4"><EmptyState title="No published signals to measure yet" description="Performance appears once signals you can view have been published and resolved." /></div>
      ) : (
        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          {rows.map((r) => {
            const entered = Number(r.wins) + Number(r.losses) + Number(r.expired_closed);
            const enough = entered >= MIN_SAMPLE;
            const symbol = (r.market_pairs as unknown as { symbol: string } | null)?.symbol ?? r.pair_id;
            return (
              <Card key={`${r.pair_id}-${r.timeframe}-${r.strategy_id}`}>
                <CardHeader title={`${symbol} · ${r.timeframe}`} action={enough ? <Badge tone="positive">n = {entered}</Badge> : <Badge tone="warning">Insufficient sample (n = {entered})</Badge>} />
                <CardBody>
                  <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-3">
                    <div><dt className="text-faint">Signals</dt><dd className="tabular-nums">{r.signals} ({r.closed} closed)</dd></div>
                    <div><dt className="text-faint">Target hit / stopped</dt><dd className="tabular-nums">{r.wins} / {r.losses}</dd></div>
                    <div><dt className="text-faint">Horizon / no entry / invalid</dt><dd className="tabular-nums">{r.expired_closed} / {r.expired_no_entry} / {r.invalidated}</dd></div>
                    <div><dt className="text-faint">Win rate</dt><dd className="tabular-nums">{enough ? `${((Number(r.wins) / entered) * 100).toFixed(1)}%` : "—"}</dd></div>
                    <div><dt className="text-faint">Avg R per trade</dt><dd className="tabular-nums">{enough && r.avg_r_multiple !== null ? Number(r.avg_r_multiple).toFixed(2) : "—"}</dd></div>
                    <div><dt className="text-faint">Period</dt><dd className="text-xs">{fmtDate(r.first_published_at)} – {fmtDate(r.last_published_at)}</dd></div>
                  </dl>
                </CardBody>
              </Card>
            );
          })}
        </div>
      )}
      <RiskDisclosure />
    </Container>
  );
}
