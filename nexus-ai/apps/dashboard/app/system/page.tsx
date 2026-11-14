"use client";

import { Badge, Card, Loading, PageHeader, Stat, Table, statusTone } from "@/components/ui";
import { time } from "@/lib/format";
import { useApi } from "@/lib/hooks";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

export default function SystemPage() {
  const { data, error, loading } = useApi<Any>("/system", 15000);
  return (
    <>
      <PageHeader title="System monitoring" subtitle="Service heartbeats and operational events" />
      <Loading error={error} loading={loading && !data} />
      {data && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label="Stored candles" value={Number(data.counts.candles).toLocaleString()} />
            <Stat label="Orders" value={data.counts.orders} />
            <Stat label="Signals" value={data.counts.signals} />
            <Stat label="Server time" value={<span className="text-sm">{time(data.server_time)}</span>} />
          </div>
          <Card title="Heartbeats">
            <ul className="grid gap-2 text-sm md:grid-cols-2">
              {Object.entries(data.heartbeats as Record<string, string | null>).map(([k, v]) => (
                <li key={k} className="flex justify-between rounded-lg bg-elevated px-3 py-2">
                  <span>{k}</span>{v ? <Badge tone="good">{time(v)}</Badge> : <Badge tone="muted">not running</Badge>}
                </li>
              ))}
            </ul>
          </Card>
          <Card title="System events">
            <Table rows={data.events} empty="No events." columns={[
              { key: "created_at", label: "Time", render: (e: Any) => time(e.created_at) },
              { key: "component", label: "Component" }, { key: "event_type", label: "Event" },
              { key: "severity", label: "Severity", render: (e: Any) => <Badge tone={statusTone(e.severity)}>{e.severity}</Badge> },
              { key: "message", label: "Message" },
            ]} />
          </Card>
        </div>
      )}
    </>
  );
}
