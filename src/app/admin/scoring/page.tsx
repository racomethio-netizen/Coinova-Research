import { redirect } from "next/navigation";
import { Card, CardBody, CardHeader, Notice, PageHeader } from "@/components/ui";
import { DEFAULT_SCORING_CONFIG, type ScoringConfig } from "@/config/scoring";
import { requireStaffPage } from "@/lib/auth/session";
import { fmtDate } from "@/lib/format";
import { validateScoringConfig } from "@/lib/research/scoring";
import { ScoringForm } from "../client";

export default async function ScoringAdminPage() {
  const viewer = await requireStaffPage();
  if (!viewer.isAdmin) redirect("/admin/research");
  const { data: configs } = await viewer.supabase.from("scoring_configs").select("version, methodology_version, config, is_active, notes, created_at").order("version", { ascending: false }).limit(20);
  const active = configs?.find((c) => c.is_active);
  let current: ScoringConfig = DEFAULT_SCORING_CONFIG;
  try {
    if (active) current = validateScoringConfig(active.config);
  } catch {
    /* fall back to default */
  }
  return (
    <div className="space-y-6">
      <PageHeader title="Scoring configuration" description="Weights are provisional. Publishing creates a new versioned configuration; earlier scores keep their recorded version." />
      {!active ? <Notice>No configuration has been published; the built-in default (v{DEFAULT_SCORING_CONFIG.methodologyVersion}) is in use.</Notice> : null}
      <Card>
        <CardHeader title="Publish a new configuration" />
        <CardBody><ScoringForm current={current} /></CardBody>
      </Card>
      <Card>
        <CardHeader title="History" />
        <CardBody>
          <ul className="space-y-1 text-sm">
            {(configs ?? []).map((c) => (
              <li key={c.version}>v{c.methodology_version} (config #{c.version}) {c.is_active ? "· active" : ""} · {fmtDate(c.created_at, { time: true })} {c.notes ? `· ${c.notes}` : ""}</li>
            ))}
            {(configs ?? []).length === 0 ? <li className="text-muted">None.</li> : null}
          </ul>
        </CardBody>
      </Card>
    </div>
  );
}
