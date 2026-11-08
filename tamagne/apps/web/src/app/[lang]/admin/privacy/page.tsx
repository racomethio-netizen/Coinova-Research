import Link from "next/link";
import { notFound } from "next/navigation";
import { ActionForm } from "@/components/admin/action-form";
import { AdminPage, ApiErrorCard, Pill, fmtDate } from "@/components/admin/shell";
import { Card } from "@/components/ui/card";
import { hasLocale } from "@/i18n/dictionaries";
import { adminGet } from "@/lib/server-api";

interface Dsr { id: string; type: "ACCESS" | "CORRECTION" | "DELETION"; status: string; details: string | null; outcome: string | null; dueAt: string; overdue: boolean; createdAt: string; userStatus: string; seller: { tamagneId: string; displayName: string } | null }

export default async function PrivacyRequests({ params }: PageProps<"/[lang]/admin/privacy">) {
  const { lang } = await params;
  if (!hasLocale(lang)) notFound();
  return (
    <AdminPage lang={lang} active="/privacy" roles={["SUPER_ADMIN", "SUPPORT_OFFICER"]} title="Privacy requests">
      {async () => {
        const r = await adminGet<Dsr[]>("/admin/privacy/requests");
        if (!r.ok) return <ApiErrorCard message={r.message} />;
        return (
          <>
            <Card>
              <p className="text-sm">
                <strong>Access</strong> requests are self-service (sellers download their data). <strong>Correction</strong> requests are handled here. <strong>Deletion</strong> requests lock the account at once and are carried out automatically after the grace period; you can only cancel them (for example if the seller asks you to), after confirming the person&apos;s identity.
              </p>
            </Card>
            {r.data.length === 0 ? <Card><p className="text-sm text-muted">No open requests.</p></Card> : null}
            {r.data.map((d) => (
              <Card key={d.id} data-testid="dsr-item">
                <p>
                  <Pill>{d.type.toLowerCase()}</Pill> <Pill>{d.status.toLowerCase().replace("_", " ")}</Pill> {d.overdue ? <Pill tone="bad">overdue</Pill> : null}{" "}
                  <span className="text-sm text-muted">received {fmtDate(d.createdAt)} · due {fmtDate(d.dueAt)}</span>
                </p>
                <p className="mt-2 text-sm">
                  {d.seller ? <Link className="font-semibold text-emerald-700" href={`/${lang}/admin/sellers/${d.seller.tamagneId}`}>{d.seller.displayName} ({d.seller.tamagneId})</Link> : "No seller profile"} · account {d.userStatus.toLowerCase().replace("_", " ")}
                </p>
                {d.details ? <p className="mt-2 whitespace-pre-wrap text-sm">{d.details}</p> : null}
                <div className="mt-3">
                  {d.type === "DELETION" ? (
                    <ActionForm method="PATCH" path={`/admin/privacy/requests/${d.id}`} submitLabel="Cancel deletion" variant="outline" extra={{ status: "REJECTED" }} confirm="Cancel this deletion and restore the account?" fields={[{ name: "outcome", label: "Why (e.g. seller asked to cancel; identity confirmed how)", type: "textarea", required: true }]} />
                  ) : (
                    <ActionForm method="PATCH" path={`/admin/privacy/requests/${d.id}`} submitLabel="Update" fields={[
                      { name: "status", label: "Status", type: "select", options: [{ value: "IN_PROGRESS", label: "In progress" }, { value: "COMPLETED", label: "Completed" }, { value: "REJECTED", label: "Rejected" }] },
                      { name: "outcome", label: "Outcome (required to close; shown to the seller)", type: "textarea" },
                    ]} />
                  )}
                </div>
              </Card>
            ))}
          </>
        );
      }}
    </AdminPage>
  );
}
