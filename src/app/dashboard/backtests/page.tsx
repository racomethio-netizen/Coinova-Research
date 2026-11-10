import type { Metadata } from "next";
import Link from "next/link";
import { Badge, ButtonLink, Card, CardBody, CardHeader, EmptyState, Notice, PageHeader } from "@/components/ui";
import { hasFeature, requireUserPage } from "@/lib/auth/session";
import { defaultBacktestPeriod } from "@/lib/backtests/schema";
import { fmtDate } from "@/lib/format";
import { listPairs } from "@/server/nexus";
import { BacktestForm } from "./client";

export const metadata: Metadata = { title: "Backtests" };

export default async function BacktestsPage() {
  const viewer = await requireUserPage("/dashboard/backtests");
  if (!hasFeature(viewer, "backtesting")) {
    return (
      <div className="space-y-6">
        <PageHeader title="Backtests" />
        <EmptyState title="Backtesting is a Premium feature" description="Test the strategy and your own bounded parameter sets on stored history, with all assumptions shown." action={<ButtonLink href="/pricing">View plans</ButtonLink>} />
      </div>
    );
  }
  const [pairs, runs] = await Promise.all([
    listPairs(viewer.supabase),
    viewer.supabase.from("backtest_runs").select("id, pair_id, timeframe, period_start, period_end, status, error, created_at").eq("requested_by", viewer.user.id).order("created_at", { ascending: false }).limit(20),
  ]);
  const symbols = new Map(pairs.map((p) => [p.id, p.symbol]));
  const period = defaultBacktestPeriod();
  return (
    <div className="space-y-6">
      <PageHeader title="Backtests" description="Hypothetical results on stored candles. Decisions use only data available at each candle close; fills, fees and slippage follow the same rules as live signals." />
      <Notice tone="warning" title="Hypothetical performance">Backtests are simulations with known limitations (no liquidity or latency effects, realized equity only). They do not predict future results and are never shown as live performance.</Notice>
      <Card><CardHeader title="New backtest" description="Up to 10 per day, one at a time, periods up to 5 years." /><CardBody><BacktestForm pairs={pairs.map((p) => ({ id: p.id, symbol: p.symbol, timeframes: p.timeframes }))} defaultStart={period.start} defaultEnd={period.end} /></CardBody></Card>
      <Card>
        <CardHeader title="Your backtests" />
        <CardBody>
          {runs.data?.length ? (
            <ul className="divide-y divide-line text-sm">
              {runs.data.map((r) => (
                <li key={r.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                  <Link className="hover:text-accent" href={`/dashboard/backtests/${r.id}`}>{symbols.get(r.pair_id) ?? r.pair_id} · {r.timeframe} · {fmtDate(r.period_start)} – {fmtDate(r.period_end)}</Link>
                  <span className="flex items-center gap-2"><Badge tone={r.status === "succeeded" ? "positive" : r.status === "failed" ? "negative" : "accent"}>{r.status}</Badge>{r.error ? <span className="text-xs text-faint">{r.error}</span> : null}</span>
                </li>
              ))}
            </ul>
          ) : <p className="text-sm text-muted">No backtests yet.</p>}
        </CardBody>
      </Card>
    </div>
  );
}
