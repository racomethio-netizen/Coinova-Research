import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { z } from "zod";
import { LineChart } from "@/components/market/line-chart";
import { OutcomeBadge, Price } from "@/components/nexus/bits";
import { Badge, Card, CardBody, CardHeader, Notice, PageHeader } from "@/components/ui";
import { requireUserPage } from "@/lib/auth/session";
import { fmtDate } from "@/lib/format";

export const metadata: Metadata = { title: "Backtest" };

type Metrics = Record<string, unknown> & {
  trades: number; signals: number; insufficient_sample: boolean; min_sample: number; win_rate: string | null; net_return: string | null;
  profit_factor: string | null; max_drawdown: string | null; expectancy_r: string | null; avg_win: string | null; avg_loss: string | null;
  sharpe: { value: string | null; basis: string }; confidence_intervals_95: Record<string, [string, string] | null>; outcomes: Record<string, number>;
};

const pct = (v: string | null | undefined) => (v === null || v === undefined ? "—" : `${(Number(v) * 100).toFixed(2)}%`);

export default async function BacktestPage({ params }: { params: Promise<{ id: string }> }) {
  const id = z.uuid().safeParse((await params).id);
  if (!id.success) notFound();
  const viewer = await requireUserPage("/dashboard/backtests");
  const [run, result, trades] = await Promise.all([
    viewer.supabase.from("backtest_runs").select("*").eq("id", id.data).maybeSingle(),
    viewer.supabase.from("backtest_results").select("*").eq("run_id", id.data).maybeSingle(),
    viewer.supabase.from("backtest_trades").select("*").eq("run_id", id.data).order("seq").limit(500),
  ]);
  if (!run.data) notFound();
  const m = result.data?.metrics as Metrics | undefined;
  const ci = m?.confidence_intervals_95 ?? {};
  return (
    <div className="space-y-6">
      <PageHeader eyebrow={<Link href="/dashboard/backtests" className="text-accent">← Backtests</Link>} title={`Backtest · ${run.data.timeframe}`} description={`${fmtDate(run.data.period_start)} – ${fmtDate(run.data.period_end)} · status ${run.data.status}`} />
      <Notice tone="warning" title="Hypothetical results">Simulated on stored candles with the assumptions below. Not live performance and not a prediction.</Notice>
      {run.data.status !== "succeeded" || !m ? (
        <Card><CardBody><p className="text-sm">{run.data.status === "failed" ? `This backtest failed: ${run.data.error ?? "unknown error"}` : "This backtest is queued or running. Refresh in a moment."}</p></CardBody></Card>
      ) : (
        <>
          {m.insufficient_sample ? <Notice tone="warning" title={`Insufficient sample: ${m.trades} trades (minimum ${m.min_sample})`}>Rates and averages from so few trades are not meaningful. Treat this run as a sanity check only.</Notice> : null}
          <Card>
            <CardHeader title="Results" action={<Badge>{m.trades} trades · {m.signals} signals</Badge>} />
            <CardBody>
              <dl className="grid grid-cols-2 gap-4 text-sm sm:grid-cols-4">
                {[
                  ["Net return", pct(m.net_return)], ["Max drawdown", pct(m.max_drawdown)], ["Profit factor", m.profit_factor ?? "—"],
                  ["Win rate", m.insufficient_sample ? "—" : pct(m.win_rate)], ["Avg win", pct(m.avg_win)], ["Avg loss", pct(m.avg_loss)],
                  ["Expectancy (R)", m.expectancy_r ?? "—"], ["Sharpe", m.sharpe?.value ?? "—"],
                ].map(([k, v]) => <div key={k}><dt className="text-faint">{k}</dt><dd className="tabular-nums">{v}</dd></div>)}
              </dl>
              <p className="mt-3 text-xs text-faint">95% bootstrap intervals: win rate {ci.win_rate ? `${pct(ci.win_rate[0])}–${pct(ci.win_rate[1])}` : "—"}; expectancy {ci.expectancy_r ? `${Number(ci.expectancy_r[0]).toFixed(2)}–${Number(ci.expectancy_r[1]).toFixed(2)} R` : "—"}. Sharpe basis: {m.sharpe?.basis}.</p>
            </CardBody>
          </Card>
          <Card><CardHeader title="Equity (realized, hypothetical)" /><CardBody><LineChart points={((result.data?.equity_curve as [string, string][]) ?? []).map(([t, v]) => ({ t: Date.parse(t), v: Number(v) }))} label="Backtest equity curve" empty="No trades closed." /></CardBody></Card>
          <Card>
            <CardHeader title="Baselines" description="Same period and costs. The strategy is only interesting if it beats these." />
            <CardBody><pre className="overflow-x-auto rounded-md bg-subtle p-3 text-xs">{JSON.stringify(result.data?.baselines, null, 2)}</pre></CardBody>
          </Card>
          <Card>
            <CardHeader title="Assumptions" />
            <CardBody><pre className="overflow-x-auto rounded-md bg-subtle p-3 text-xs">{JSON.stringify(result.data?.assumptions, null, 2)}</pre></CardBody>
          </Card>
          <Card className="overflow-x-auto">
            <CardHeader title="Trades" />
            <table className="w-full min-w-[720px] text-sm">
              <thead className="text-left text-xs uppercase text-faint"><tr>{["#", "Side", "Decided", "Outcome", "Entry", "Exit", "Net", "R"].map((h) => <th key={h} scope="col" className="px-3 py-2">{h}</th>)}</tr></thead>
              <tbody className="divide-y divide-line">
                {(trades.data ?? []).map((t) => (
                  <tr key={t.seq}>
                    <td className="px-3 py-2">{t.seq}</td><td className="px-3 py-2">{t.direction}</td><td className="px-3 py-2 text-xs">{fmtDate(t.decided_at, { time: true })}</td>
                    <td className="px-3 py-2"><OutcomeBadge outcome={t.outcome} /></td><td className="px-3 py-2"><Price v={t.entry_price} /></td><td className="px-3 py-2"><Price v={t.exit_price} /></td>
                    <td className="px-3 py-2 tabular-nums">{pct(t.net_return)}</td><td className="px-3 py-2 tabular-nums">{t.r_multiple ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        </>
      )}
    </div>
  );
}
