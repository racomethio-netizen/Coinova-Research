"use client";

import { Badge, Card, Loading, PageHeader, Table, statusTone } from "@/components/ui";
import { DASH, num, pct, qty, time } from "@/lib/format";
import { useApi, useMode } from "@/lib/hooks";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;


export default function Signals() {
  const { mode } = useMode();
  const { data, error, loading } = useApi<Any[]>(`/signals?mode=${mode}`, 15000);
  return (
    <>
      <PageHeader title="AI signals" subtitle="Every strategy signal and the risk engine's decision, with reasons" />
      <Loading error={error} loading={loading && !data} />
      <Card>
        <Table rows={data} empty="No signals. Strategies trade only after passing out-of-sample validation; none has passed yet." columns={[
          { key: "generated_at", label: "Time", render: (s: Any) => time(s.generated_at) },
          { key: "strategy", label: "Strategy" }, { key: "symbol", label: "Pair" }, { key: "timeframe", label: "TF" },
          { key: "entry_price", label: "Entry", numeric: true, render: (s: Any) => num(s.entry_price) },
          { key: "stop_loss", label: "Stop", numeric: true, render: (s: Any) => num(s.stop_loss) },
          { key: "take_profit", label: "Target", numeric: true, render: (s: Any) => num(s.take_profit) },
          { key: "confidence", label: "Score", numeric: true, render: (s: Any) => pct(s.confidence, 0) },
          { key: "status", label: "Decision", render: (s: Any) => <Badge tone={statusTone(s.status)}>{s.status}</Badge> },
          { key: "rejection_reason", label: "Reason" },
        ]} />
      </Card>
    </>
  );
}
