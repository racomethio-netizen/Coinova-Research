import { Badge, Card, CardBody, CardHeader, Notice, PageHeader } from "@/components/ui";
import { requireStaffPage } from "@/lib/auth/session";
import { fmtDate } from "@/lib/format";
import { ActionButton, TermsReviewForm } from "../nexus-client";

export default async function AdminMarketPage() {
  const viewer = await requireStaffPage();
  const [{ data: sources }, { data: pairs }] = await Promise.all([
    viewer.supabase.from("market_sources").select("*").order("id"),
    viewer.supabase.from("market_pairs").select("id, symbol, timeframes, enabled, market_sources(name)").order("id"),
  ]);
  return (
    <div className="space-y-6">
      <PageHeader title="Market data" description="Sources, their terms review (gate G-4), and tradable pairs." />
      <Notice tone="warning" title="Terms review">Production ingestion refuses a source until its terms review is recorded. See docs/nexus/MARKET_DATA_SOURCES.md. This is a compliance record, not a formality.</Notice>
      {(sources ?? []).map((s) => (
        <Card key={s.id}>
          <CardHeader title={s.name} description={<span className="font-mono text-xs">{s.base_url}</span>} action={s.terms_reviewed_at ? <Badge tone="positive">Reviewed {fmtDate(s.terms_reviewed_at)}</Badge> : <Badge tone="warning">Not reviewed</Badge>} />
          <CardBody className="space-y-3 text-sm">
            {s.terms_url ? <p>Terms: <a className="text-accent underline" href={s.terms_url} target="_blank" rel="noopener noreferrer nofollow">{s.terms_url}</a></p> : null}
            {s.terms_notes ? <p className="whitespace-pre-wrap text-muted">{s.terms_notes}</p> : null}
            {viewer.isAdmin && !s.terms_reviewed_at ? <TermsReviewForm sourceKey={s.key} /> : null}
          </CardBody>
        </Card>
      ))}
      <Card>
        <CardHeader title="Pairs" />
        <CardBody>
          <ul className="divide-y divide-line text-sm">
            {(pairs ?? []).map((p) => (
              <li key={p.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                <span><span className="font-medium">{p.symbol}</span> <span className="text-faint">{(p.timeframes as string[]).join(", ")}</span> <Badge tone={p.enabled ? "positive" : "neutral"}>{p.enabled ? "Enabled" : "Disabled"}</Badge></span>
                {viewer.isAdmin ? <ActionButton url={`/api/admin/pairs/${p.id}`} body={{ enabled: !p.enabled }} label={p.enabled ? "Disable" : "Enable"} variant="ghost" /> : null}
              </li>
            ))}
          </ul>
        </CardBody>
      </Card>
    </div>
  );
}
