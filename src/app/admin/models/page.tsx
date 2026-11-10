import { Badge, Card, CardBody, EmptyState, Notice, PageHeader } from "@/components/ui";
import { requireStaffPage } from "@/lib/auth/session";
import { fmtDate } from "@/lib/format";
import { ActionButton } from "../nexus-client";

export default async function AdminModelsPage() {
  const viewer = await requireStaffPage();
  const [{ data: models }, { data: strategies }] = await Promise.all([
    viewer.supabase.from("model_versions").select("*").order("created_at", { ascending: false }),
    viewer.supabase.from("strategies").select("id, key, version, name, created_at").order("created_at", { ascending: false }),
  ]);
  return (
    <div className="space-y-6">
      <PageHeader title="Strategies & models" description="Strategy versions are immutable. Models move draft → shadow → approved → retired." />
      <Notice tone="warning" title="Approval rules (enforced by the database)">
        A model can only be approved with an artifact checksum and a validation report that includes a lockbox result. Approval is recorded under your account and in the audit log, and cannot be edited afterwards.
      </Notice>
      <Card><CardBody>
        <h2 className="mb-2 text-sm font-semibold">Strategy versions</h2>
        <ul className="text-sm">{(strategies ?? []).map((s) => <li key={s.id}>{s.name} <span className="font-mono text-xs text-faint">{s.key} {s.version}</span> · {fmtDate(s.created_at)}</li>)}</ul>
      </CardBody></Card>
      {!models?.length ? <EmptyState title="No model versions" description="Models are registered by the offline training pipeline after walk-forward validation." /> : models.map((m) => (
        <Card key={m.id}>
          <CardBody className="flex flex-wrap items-center justify-between gap-3 text-sm">
            <span><span className="font-medium">{m.model_type} {m.version}</span> <Badge tone={m.status === "approved" ? "positive" : m.status === "retired" ? "neutral" : "warning"}>{m.status}</Badge>
              <span className="ml-2 font-mono text-xs text-faint">sha256 {m.artifact_sha256 ?? "—"} · report {m.validation_report_id ?? "—"}</span></span>
            {viewer.isAdmin ? (
              <span className="flex gap-2">
                {m.status === "draft" ? <ActionButton url={`/api/admin/models/${m.id}`} body={{ status: "shadow" }} label="Move to shadow" /> : null}
                {m.status === "shadow" ? <ActionButton url={`/api/admin/models/${m.id}`} body={{ status: "approved" }} label="Approve" variant="primary" confirm="Approve this model? Approval is permanent and audited." /> : null}
                {m.status !== "retired" ? <ActionButton url={`/api/admin/models/${m.id}`} body={{ status: "retired" }} label="Retire" variant="ghost" confirm="Retire this model?" /> : null}
              </span>
            ) : null}
          </CardBody>
        </Card>
      ))}
    </div>
  );
}
