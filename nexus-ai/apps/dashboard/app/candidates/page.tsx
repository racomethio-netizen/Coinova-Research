"use client";

import { Badge, Card, Loading, PageHeader } from "@/components/ui";
import { DASH, time } from "@/lib/format";
import { useApi } from "@/lib/hooks";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

const TONE: Record<string, "good" | "warn" | "bad" | "muted"> = { LONG: "good", SHORT: "bad", WAIT: "warn", NO_TRADE: "muted" };
const LABEL: Record<string, string> = { LONG: "LONG", SHORT: "SHORT", WAIT: "WAIT", NO_TRADE: "NO TRADE" };

function pct(v: number | null | undefined, d = 2): string {
  return v === null || v === undefined ? DASH : `${(v * 100).toFixed(d)}%`;
}

function List({ title, items, tone }: { title: string; items: string[]; tone?: string }) {
  if (!items?.length) return null;
  return (
    <div className="mt-2">
      <p className="text-xs font-medium text-muted">{title}</p>
      <ul className={`list-disc pl-5 text-sm ${tone ?? ""}`}>{items.map((x) => <li key={x}>{x}</li>)}</ul>
    </div>
  );
}

function SignalCard({ s }: { s: Any }) {
  const strategies = Object.entries((s.sources?.strategies ?? {}) as Record<string, Any>);
  const eligible = strategies.filter(([, v]) => v.eligible);
  return (
    <Card title={`${s.symbol}`} actions={<Badge tone={TONE[s.decision] ?? "muted"}>{LABEL[s.decision] ?? s.decision}</Badge>}>
      {s.decision === "LONG" ? (
        <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-sm md:grid-cols-4">
          <dt className="text-muted">Strategy</dt><dd>{s.strategy} ({s.timeframe})</dd>
          <dt className="text-muted">Entry</dt><dd className="num">{s.entry}</dd>
          <dt className="text-muted">Stop</dt><dd className="num">{s.stop}</dd>
          <dt className="text-muted">Target</dt><dd className="num">{s.target}</dd>
          <dt className="text-muted">Reward/risk</dt><dd className="num">{s.reward_risk?.toFixed(2) ?? DASH}</dd>
          <dt className="text-muted">Probability</dt><dd className="num">{pct(s.probability, 0)}</dd>
          <dt className="text-muted">Expected return</dt><dd className="num">{pct(s.expected_return, 3)}</dd>
          <dt className="text-muted">Expires</dt><dd>{s.expires_at ? time(s.expires_at) : DASH}</dd>
        </dl>
      ) : null}
      {s.probability_source && <p className="mt-1 text-xs text-muted">Probability from: {s.probability_source}</p>}
      <List title="Why" items={s.reasons} />
      <List title="Blockers" items={s.blockers} tone="text-down" />
      <List title="Conflicts (context, not votes)" items={s.conflicts} tone="text-warn" />
      <p className="mt-2 text-xs text-muted">Regime {s.regime ?? DASH} · {eligible.length} of {strategies.length} strategies eligible · setups on the last closed bar: {(s.sources?.setups_now ?? []).join(", ") || "none"} · model: {s.sources?.model?.available ? `v${s.sources.model.version}` : s.sources?.model?.reason ?? DASH}</p>
      {strategies.length > 0 && (
        <details className="mt-2 text-sm">
          <summary className="cursor-pointer text-muted">Strategy selector verdicts</summary>
          <table className="mt-2 w-full text-xs">
            <thead><tr className="text-left text-muted"><th className="pr-2">Strategy</th><th className="pr-2">Eligible</th><th className="pr-2">Robust exp.</th><th>Why not</th></tr></thead>
            <tbody>
              {strategies.map(([code, v]) => (
                <tr key={code} className="border-t border-line align-top">
                  <td className="py-1 pr-2">{code}</td>
                  <td className="pr-2">{v.eligible ? "yes" : "no"}</td>
                  <td className="num pr-2">{pct(v.robust_expectancy, 3)}</td>
                  <td>{(v.why_not as string[]).join("; ") || DASH}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </Card>
  );
}

export default function Candidates() {
  const { data, error, loading } = useApi<Any>("/decision/fusion", 60000);
  return (
    <>
      <PageHeader title="Trade candidates" subtitle="Signal fusion: LONG, SHORT, WAIT or NO TRADE per pair, with the reasons. Only strategies or models with validated out-of-sample edges can create a trade; the risk engine still decides afterwards." />
      <Loading error={error} loading={loading && !data} />
      {data && (
        <div className="space-y-4">
          <p className="text-sm text-muted">{data.principle} Generated {time(data.generated_at)}.</p>
          <div className="grid gap-4 md:grid-cols-2">
            {(data.signals as Any[]).map((s) => <SignalCard key={s.symbol} s={s} />)}
          </div>
        </div>
      )}
    </>
  );
}
