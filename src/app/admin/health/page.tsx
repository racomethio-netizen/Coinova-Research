import { Badge, Card, CardBody, CardHeader, PageHeader } from "@/components/ui";
import { requireStaffPage } from "@/lib/auth/session";
import { getNexusGates } from "@/lib/env";
import { fmtDate, fmtRelative } from "@/lib/format";
import { listPairs, TIMEFRAMES } from "@/server/nexus";

const TF_MS: Record<string, number> = { "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000 };

export default async function AdminHealthPage() {
  const viewer = await requireStaffPage();
  const sb = viewer.supabase;
  const pairs = await listPairs(sb);
  const [checks, beats, gaps, runs, experiments, queue] = await Promise.all([
    sb.rpc("ops_health"),
    sb.from("engine_heartbeats").select("*").order("component"),
    sb.from("candle_gaps").select("pair_id, timeframe, gap_start, gap_end, status, attempts").neq("status", "backfilled").order("gap_start", { ascending: false }).limit(20),
    sb.from("ingest_runs").select("id, kind, status, pair_id, timeframe, candles_upserted, candles_rejected, revisions_detected, started_at").order("started_at", { ascending: false }).limit(20),
    sb.from("experiments").select("id, kind, status, started_at, error").order("started_at", { ascending: false }).limit(10),
    sb.from("backtest_runs").select("status").in("status", ["queued", "running"]),
  ]);
  const freshness = await Promise.all(
    pairs.flatMap((p) => TIMEFRAMES.filter((t) => p.timeframes.includes(t)).map(async (tf) => {
      const { data } = await sb.from("market_candles").select("open_time").eq("pair_id", p.id).eq("timeframe", tf).eq("is_final", true).order("open_time", { ascending: false }).limit(1).maybeSingle();
      return { symbol: p.symbol, tf, last: data?.open_time ?? null };
    })),
  );
  const gates = getNexusGates();
  // eslint-disable-next-line react-hooks/purity -- server-rendered status page; "now" is the request time
  const now = Date.now();
  return (
    <div className="space-y-6">
      <PageHeader title="System health" description="Engine heartbeats, data freshness, gaps, ingestion and research runs." />
      <Card><CardBody className="flex flex-wrap gap-2 text-sm">
        <Badge tone="accent">SIGNALS_VISIBILITY={gates.signalsVisibility}</Badge><Badge>PAID_SIGNALS_ENABLED={String(gates.paidSignalsEnabled)}</Badge><Badge tone="positive">LIVE_EXECUTION_ENABLED=false</Badge>
        <Badge>Backtests queued/running: {queue.data?.length ?? 0}</Badge>
      </CardBody></Card>
      <Card>
        <CardHeader title="Operations checks" description="The same checks the watchdog runs every 5 minutes; admins get an alert per check and hour. Steps for each are in docs/nexus/RUNBOOKS.md." />
        <CardBody>
          {checks.error ? (
            <p className="text-sm text-negative">Checks could not be loaded.</p>
          ) : checks.data?.length ? (
            <ul className="space-y-1 text-sm">
              {(checks.data as { check_key: string; severity: string; detail: string }[]).map((c) => (
                <li key={c.check_key}>
                  <Badge tone={c.severity === "critical" ? "negative" : "warning"}>{c.severity}</Badge> <span className="font-mono text-xs">{c.check_key}</span> {c.detail}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-positive">All checks pass.</p>
          )}
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Heartbeats" />
        <CardBody>{beats.data?.length ? <ul className="text-sm">{beats.data.map((b) => <li key={b.component}>{b.component}: {fmtRelative(b.last_seen)} <span className="font-mono text-xs text-faint">{JSON.stringify(b.details)}</span></li>)}</ul> : <p className="text-sm text-negative">No engine heartbeat recorded. The worker is not running.</p>}</CardBody>
      </Card>
      <Card className="overflow-x-auto">
        <CardHeader title="Data freshness" description="A timeframe is stale when its latest final candle is older than two candles." />
        <table className="w-full min-w-[480px] text-sm"><thead className="text-left text-xs uppercase text-faint"><tr><th scope="col" className="px-3 py-2">Pair</th><th scope="col" className="px-3 py-2">TF</th><th scope="col" className="px-3 py-2">Latest candle</th><th scope="col" className="px-3 py-2">State</th></tr></thead>
          <tbody className="divide-y divide-line">{freshness.map((f) => {
            const stale = !f.last || now - Date.parse(f.last) > 3 * TF_MS[f.tf]!;
            return <tr key={`${f.symbol}-${f.tf}`}><td className="px-3 py-2">{f.symbol}</td><td className="px-3 py-2">{f.tf}</td><td className="px-3 py-2">{f.last ? fmtDate(f.last, { time: true }) : "none"}</td><td className="px-3 py-2"><Badge tone={stale ? "negative" : "positive"}>{stale ? "Stale" : "Fresh"}</Badge></td></tr>;
          })}</tbody></table>
      </Card>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card><CardHeader title="Open or unrecoverable gaps" /><CardBody>{gaps.data?.length ? <ul className="text-sm">{gaps.data.map((g) => <li key={`${g.pair_id}-${g.timeframe}-${g.gap_start}`}>pair {g.pair_id} {g.timeframe}: {fmtDate(g.gap_start, { time: true })} → {fmtDate(g.gap_end, { time: true })} <Badge tone={g.status === "open" ? "warning" : "negative"}>{g.status}</Badge> ({g.attempts} attempts)</li>)}</ul> : <p className="text-sm text-muted">None.</p>}</CardBody></Card>
        <Card><CardHeader title="Recent ingestion runs" /><CardBody>{runs.data?.length ? <ul className="text-sm">{runs.data.map((r) => <li key={r.id}>{fmtDate(r.started_at, { time: true })} {r.kind} {r.timeframe ?? ""} <Badge tone={r.status === "succeeded" ? "positive" : r.status === "running" ? "accent" : "negative"}>{r.status}</Badge> +{r.candles_upserted} / rejected {r.candles_rejected} / revisions {r.revisions_detected}</li>)}</ul> : <p className="text-sm text-muted">No runs yet.</p>}</CardBody></Card>
      </div>
      <Card><CardHeader title="Recent experiments" description="Every research run is kept, including failures." /><CardBody>{experiments.data?.length ? <ul className="text-sm">{experiments.data.map((e) => <li key={e.id}>{fmtDate(e.started_at, { time: true })} {e.kind} <Badge tone={e.status === "succeeded" ? "positive" : e.status === "failed" ? "negative" : "accent"}>{e.status}</Badge> {e.error ?? ""}</li>)}</ul> : <p className="text-sm text-muted">No experiments yet.</p>}</CardBody></Card>
    </div>
  );
}
