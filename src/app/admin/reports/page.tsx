import { Badge, Card, CardBody, EmptyState, PageHeader } from "@/components/ui";
import { requireStaffPage } from "@/lib/auth/session";
import { fmtDate } from "@/lib/format";
import { ActionButton } from "../nexus-client";

export default async function AdminReportsPage() {
  const viewer = await requireStaffPage();
  const { data } = await viewer.supabase.from("abuse_reports").select("*").order("created_at", { ascending: false }).limit(100);
  return (
    <div className="space-y-6">
      <PageHeader title="Abuse reports" description="Reports filed by users. Handling is recorded under your account and in the audit log." />
      {!data?.length ? <EmptyState title="No reports" /> : data.map((r) => (
        <Card key={r.id}>
          <CardBody className="space-y-2 text-sm">
            <div className="flex flex-wrap items-center gap-2"><Badge tone={r.status === "open" ? "warning" : "neutral"}>{r.status}</Badge><span className="font-medium">{r.reason}</span><span className="text-faint">{r.target_type} {r.target_id ?? ""}</span><span className="text-xs text-faint">{fmtDate(r.created_at, { time: true })}</span></div>
            {r.details ? <p className="whitespace-pre-wrap">{r.details}</p> : null}
            {viewer.isAdmin && (r.status === "open" || r.status === "reviewing") ? (
              <div className="flex flex-wrap gap-2">
                {r.status === "open" ? <ActionButton url={`/api/admin/reports/${r.id}`} body={{ status: "reviewing" }} label="Start review" /> : null}
                <ActionButton url={`/api/admin/reports/${r.id}`} body={{ status: "resolved" }} label="Resolve" variant="primary" />
                <ActionButton url={`/api/admin/reports/${r.id}`} body={{ status: "dismissed" }} label="Dismiss" variant="ghost" />
              </div>
            ) : r.resolution_note ? <p className="text-xs text-muted">Note: {r.resolution_note}</p> : null}
          </CardBody>
        </Card>
      ))}
    </div>
  );
}
