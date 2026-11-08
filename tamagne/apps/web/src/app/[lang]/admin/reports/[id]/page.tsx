import Link from "next/link";
import { notFound } from "next/navigation";
import { ActionForm } from "@/components/admin/action-form";
import { AdminPage, ApiErrorCard, Pill, fmtDate } from "@/components/admin/shell";
import { Card } from "@/components/ui/card";
import { hasLocale } from "@/i18n/dictionaries";
import { adminGet } from "@/lib/server-api";

interface Report { id: string; category: string; status: string; description: string; reporterContact: string | null; reporterSignedIn: boolean; resolutionNote: string | null; createdAt: string; resolvedAt: string | null; seller: { tamagneId: string; displayName: string; accountStatus: string } }
const NEXT: Record<string, string[]> = { SUBMITTED: ["TRIAGED", "INVESTIGATING", "DISMISSED"], TRIAGED: ["INVESTIGATING", "DISMISSED", "ACTION_TAKEN"], INVESTIGATING: ["ACTION_TAKEN", "DISMISSED"] };

export default async function ReportDetail({ params }: PageProps<"/[lang]/admin/reports/[id]">) {
  const { lang, id } = await params;
  if (!hasLocale(lang)) notFound();
  return (
    <AdminPage lang={lang} active="/reports" roles={["SUPER_ADMIN", "SUPPORT_OFFICER", "SECURITY_OFFICER"]} title="Fraud report">
      {async () => {
        const r = await adminGet<Report>(`/admin/reports/${encodeURIComponent(id)}`);
        if (!r.ok) return <ApiErrorCard message={r.message} />;
        const x = r.data;
        const next = NEXT[x.status] ?? [];
        return (
          <>
            <Card>
              <p><Pill tone="bad">{x.category.replace(/_/g, " ").toLowerCase()}</Pill> <Pill>{x.status.toLowerCase()}</Pill> <span className="text-sm text-muted">received {fmtDate(x.createdAt)}</span></p>
              <p className="mt-3">
                Seller: <Link className="font-semibold text-emerald-700" href={`/${lang}/admin/sellers/${x.seller.tamagneId}`}>{x.seller.displayName} ({x.seller.tamagneId})</Link> · account {x.seller.accountStatus.toLowerCase()}
              </p>
              <h2 className="mt-4 font-semibold">Description</h2>
              <p className="mt-1 whitespace-pre-wrap text-sm" data-testid="report-description">{x.description}</p>
              <p className="mt-3 text-sm text-muted">Reporter: {x.reporterSignedIn ? "signed-in user" : "anonymous"}{x.reporterContact ? <> · contact <span data-testid="report-contact">{x.reporterContact}</span> (viewing is logged)</> : null}</p>
              {x.resolutionNote ? <p className="mt-3 text-sm"><strong>Resolution:</strong> {x.resolutionNote} ({fmtDate(x.resolvedAt)})</p> : null}
            </Card>
            {next.length ? (
              <Card>
                <h2 className="font-semibold">Update</h2>
                <div className="mt-3">
                  <ActionForm
                    method="PATCH"
                    path={`/admin/reports/${x.id}`}
                    submitLabel="Save"
                    successText="Saved."
                    fields={[
                      { name: "status", label: "New status", type: "select", options: next.map((s) => ({ value: s, label: s.replace(/_/g, " ").toLowerCase() })) },
                      { name: "note", label: "Note (required when closing)", type: "textarea" },
                      { name: "assignToMe", label: "Assign to me", type: "checkbox" },
                    ]}
                  />
                </div>
                <p className="mt-3 text-xs text-muted">To suspend the seller, use the seller page. Reports never change verification automatically.</p>
              </Card>
            ) : null}
          </>
        );
      }}
    </AdminPage>
  );
}
