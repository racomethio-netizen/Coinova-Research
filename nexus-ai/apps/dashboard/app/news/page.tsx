"use client";

import { Badge, Card, Loading, PageHeader, Table } from "@/components/ui";
import { num, time } from "@/lib/format";
import { useApi } from "@/lib/hooks";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

export default function NewsPage() {
  const { data, error, loading } = useApi<Any>("/news", 60_000);
  return (
    <>
      <PageHeader title="News" subtitle="Public news feeds (Federal Reserve, SEC, exchange status pages) scored for high-impact keywords. A high or unknown score pauses new entries; it never opens a trade." />
      <Loading error={error} loading={loading && !data} />
      {data && (
        <div className="space-y-4">
          <Card title="News risk" actions={<Badge tone={data.blocks_entries ? "bad" : "good"}>{data.blocks_entries ? "entries paused" : "normal"}</Badge>}>
            {data.available ? (
              <dl className="grid grid-cols-2 gap-2 text-sm md:grid-cols-4">
                <dt className="text-muted">Score</dt><dd className="num">{data.score == null ? "unknown" : num(data.score, 3)} (pauses above {num(data.threshold, 2)})</dd>
                <dt className="text-muted">Updated</dt><dd>{time(data.as_of)}</dd>
                <dt className="text-muted">Matched</dt><dd>{data.matched?.length ? data.matched.join(", ") : "nothing high-impact"}</dd>
                <dt className="text-muted">Unreadable sources</dt><dd>{data.unknown_sources?.length ? data.unknown_sources.join(", ") : "none"}</dd>
              </dl>
            ) : <p role="alert" className="text-sm text-down">{data.message}</p>}
          </Card>
          <Card title="Latest items">
            <Table rows={data.items} empty="No items yet." columns={[
              { key: "published", label: "Published", render: (r: Any) => time(r.published) },
              { key: "source", label: "Source" },
              { key: "title", label: "Headline", render: (r: Any) => <a href={r.link} target="_blank" rel="noopener noreferrer" className="underline">{r.title}</a> },
              { key: "impact", label: "Impact", render: (r: Any) => r.impact > 0 ? <Badge tone={r.impact >= 0.7 ? "bad" : "warn"}>{r.keyword} ({num(r.impact, 2)})</Badge> : <span className="text-muted">none</span> },
            ]} />
          </Card>
        </div>
      )}
    </>
  );
}
