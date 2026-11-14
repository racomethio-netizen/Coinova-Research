"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { Badge, Button, Card, Loading, PageHeader } from "@/components/ui";
import { DASH, num, pct, time } from "@/lib/format";
import { useApi } from "@/lib/hooks";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

export default function Strategies() {
  const { data, error, loading, reload } = useApi<Any[]>("/strategies");
  const [msg, setMsg] = useState<string | null>(null);
  const toggle = async (code: string, enabled: boolean) => {
    setMsg(null);
    try { await api.patch(`/strategies/${code}`, { enabled }); await reload(); }
    catch (e) { setMsg(e instanceof Error ? e.message : String(e)); }
  };
  return (
    <>
      <PageHeader title="Strategy configuration" subtitle="Long-only spot strategies with multi-timeframe confirmation. Enabling requires passed out-of-sample validation." />
      <Loading error={error} loading={loading && !data} />
      {msg && <p role="alert" className="mb-3 rounded-lg border border-down/40 bg-down/10 p-3 text-sm text-down">{msg}</p>}
      <div className="grid gap-4 md:grid-cols-2">
        {data?.map((s) => {
          const v = s.parameters?.validation;
          return (
            <Card key={s.code} title={s.name} actions={<Badge tone={s.enabled ? "good" : "muted"}>{s.enabled ? "enabled" : "disabled"}</Badge>}>
              {v ? (
                <div className="space-y-2 text-sm">
                  <div className="flex items-center gap-2">Validation <Badge tone={v.passed ? "good" : "bad"}>{v.passed ? "passed" : "failed"}</Badge><span className="text-xs text-muted">{time(v.validated_at)} · {v.exchange}</span></div>
                  <dl className="grid grid-cols-2 gap-2 text-xs">
                    <div><dt className="text-muted">OOS trades</dt><dd className="num">{v.oos?.n ?? DASH}</dd></div>
                    <div><dt className="text-muted">Win rate</dt><dd className="num">{pct(v.oos?.win_rate, 1)}</dd></div>
                    <div><dt className="text-muted">Expectancy / trade</dt><dd className="num">{pct(v.oos?.expectancy, 3)}</dd></div>
                    <div><dt className="text-muted">Profit factor</dt><dd className="num">{num(v.oos?.profit_factor)}</dd></div>
                  </dl>
                  {v.failures?.length > 0 && <ul className="list-disc pl-5 text-xs text-down">{v.failures.map((f: string) => <li key={f}>{f}</li>)}</ul>}
                  {s.parameters?.params && <pre className="num overflow-x-auto rounded bg-elevated p-2 text-xs">{JSON.stringify(s.parameters.params, null, 1)}</pre>}
                </div>
              ) : <p className="text-sm text-muted">Not validated yet. Run <code>python -m nexus_backtest.cli validate-strategies</code>.</p>}
              <div className="mt-3 flex gap-2">
                {s.enabled ? <Button onClick={() => toggle(s.code, false)}>Disable</Button>
                  : <Button tone="primary" disabled={!v?.passed} onClick={() => toggle(s.code, true)}>Enable</Button>}
              </div>
            </Card>
          );
        })}
      </div>
    </>
  );
}
