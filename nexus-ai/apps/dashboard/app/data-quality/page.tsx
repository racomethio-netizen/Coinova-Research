"use client";

import { Badge, Loading, PageHeader, Stat, Table } from "@/components/ui";
import { DASH, time } from "@/lib/format";
import { useApi } from "@/lib/hooks";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

function age(s: number | null): string {
  if (s == null) return DASH;
  if (s < 120) return `${s} s`;
  if (s < 7200) return `${Math.round(s / 60)} min`;
  return `${Math.round(s / 3600)} h`;
}

export default function DataQuality() {
  const { data, error, loading } = useApi<Any>("/markets/data-quality", 30000);
  const rows: Any[] = data?.series ?? [];
  const empty = rows.filter((r) => r.newest == null).length;
  return (
    <>
      <PageHeader title="Data quality" subtitle="Every stored candle series: newest closed bar, age, coverage and gaps. Built from the database; nothing is estimated." />
      <Loading error={error} loading={loading && !data} />
      {data && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label="Series" value={rows.length} />
            <Stat label="Stale or empty" value={data.stale} tone={data.stale ? "warn" : "up"} hint={empty ? `${empty} with no data yet` : undefined} />
            <Stat label="With gaps" value={rows.filter((r) => r.missing > 0).length} />
            <Stat label="Window" value={`${data.window_bars} bars`} />
          </div>
          <p className="text-sm text-muted">{data.note} Trading stops automatically when the 5m data of a traded pair is stale.</p>
          <Table
            rows={rows}
            empty="No series configured."
            columns={[
              { key: "symbol", label: "Pair", render: (r: Any) => `${r.symbol} (${r.exchange})` },
              { key: "timeframe", label: "TF" },
              { key: "source", label: "Source" },
              { key: "status", label: "Status", render: (r: Any) => <Badge tone={r.newest == null ? "muted" : r.stale ? "bad" : "good"}>{r.newest == null ? "no data" : r.stale ? "stale" : "fresh"}</Badge> },
              { key: "newest", label: "Newest bar", render: (r: Any) => (r.newest ? time(r.newest) : DASH) },
              { key: "age_seconds", label: "Closed ago", numeric: true, render: (r: Any) => age(r.age_seconds) },
              { key: "coverage", label: "Coverage", numeric: true, render: (r: Any) => (r.coverage == null ? DASH : `${(r.coverage * 100).toFixed(1)}%`) },
              { key: "missing", label: "Missing bars", numeric: true },
              { key: "largest_gap_bars", label: "Largest gap", numeric: true },
            ]}
          />
        </div>
      )}
    </>
  );
}
