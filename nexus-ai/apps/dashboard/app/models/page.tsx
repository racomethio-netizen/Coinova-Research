"use client";

import { Badge, Card, Loading, PageHeader, Table, statusTone } from "@/components/ui";
import { DASH, num, pct, time } from "@/lib/format";
import { useApi } from "@/lib/hooks";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

export default function Models() {
  const { data, error, loading } = useApi<Any[]>("/models", 60000);
  return (
    <>
      <PageHeader title="AI model monitoring" subtitle="Versions, purged walk-forward evaluations and promotion gates. A model is never promoted on in-sample metrics." />
      <Loading error={error} loading={loading && !data} />
      {data && data.length === 0 && <Card><p className="text-sm text-muted">No models trained yet. Run <code>python -m nexus_ai.cli train</code>.</p></Card>}
      <div className="space-y-4">
        {data?.map((m) => (
          <Card key={m.id} title={`${m.name} v${m.version}`} actions={<Badge tone={statusTone(m.status)}>{m.status}</Badge>}>
            <p className="mb-2 text-xs text-muted">{m.algorithm} · {m.features} features · trained {time(m.created_at)} · artifact {m.artifact ? "saved" : "not saved (rejected)"}</p>
            {m.evaluations.map((e: Any, i: number) => (
              <div key={i} className="space-y-2">
                <dl className="grid grid-cols-2 gap-2 text-sm md:grid-cols-5">
                  <div><dt className="text-xs text-muted">Mean AUC</dt><dd className="num">{num(e.summary?.mean_auc, 3)}</dd></div>
                  <div><dt className="text-xs text-muted">Brier skill</dt><dd className="num">{num(e.summary?.brier_skill, 4)}</dd></div>
                  <div><dt className="text-xs text-muted">OOS trades</dt><dd className="num">{e.summary?.trades ?? DASH}</dd></div>
                  <div><dt className="text-xs text-muted">Win rate</dt><dd className="num">{pct(e.summary?.win_rate, 1)}</dd></div>
                  <div><dt className="text-xs text-muted">Expectancy</dt><dd className="num">{pct(e.summary?.expectancy, 3)}</dd></div>
                </dl>
                {e.failures?.length > 0 && <ul className="list-disc pl-5 text-xs text-down">{e.failures.map((f: string) => <li key={f}>{f}</li>)}</ul>}
                <Table rows={e.windows} empty="No windows." columns={[
                  { key: "test_start", label: "Test window" },
                  { key: "auc", label: "AUC", numeric: true, render: (w: Any) => num(w.auc, 3) },
                  { key: "brier", label: "Brier / ref", numeric: true, render: (w: Any) => `${num(w.brier, 4)} / ${num(w.brier_ref, 4)}` },
                  { key: "trades", label: "Trades", numeric: true },
                  { key: "expectancy", label: "Expectancy", numeric: true, render: (w: Any) => pct(w.expectancy, 3) },
                  { key: "uncond_expectancy", label: "All-bars baseline", numeric: true, render: (w: Any) => pct(w.uncond_expectancy, 3) },
                ]} />
              </div>
            ))}
          </Card>
        ))}
      </div>
    </>
  );
}
