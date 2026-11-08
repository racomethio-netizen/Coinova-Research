import Link from "next/link";
import { notFound } from "next/navigation";
import { ActionForm } from "@/components/admin/action-form";
import { AdminPage, ApiErrorCard, Pill, fmtDate } from "@/components/admin/shell";
import { Card } from "@/components/ui/card";
import { hasLocale } from "@/i18n/dictionaries";
import { adminGet } from "@/lib/server-api";

interface Appeal { id: string; subjectType: string; reason: string; status: string; createdAt: string; seller: { tamagneId: string; displayName: string; accountStatus: string; suspensionReason: string | null } }

export default async function Appeals({ params }: PageProps<"/[lang]/admin/appeals">) {
  const { lang } = await params;
  if (!hasLocale(lang)) notFound();
  return (
    <AdminPage lang={lang} active="/appeals" roles={["SUPER_ADMIN", "VERIFICATION_OFFICER", "SUPPORT_OFFICER"]} title="Appeals">
      {async () => {
        const r = await adminGet<Appeal[]>("/admin/appeals");
        if (!r.ok) return <ApiErrorCard message={r.message} />;
        return (
          <>
            <Card><p className="text-sm"><strong>Upheld</strong> = the original decision stands. <strong>Overturned</strong> = the seller is right; overturned suspensions still need a second reviewer before the account is reactivated. You cannot decide an appeal against your own decision.</p></Card>
            {r.data.length === 0 ? <Card><p className="text-sm text-muted">No open appeals.</p></Card> : null}
            {r.data.map((a) => (
              <Card key={a.id} data-testid="appeal-item">
                <p><Pill>{a.subjectType.replace(/_/g, " ").toLowerCase()}</Pill> <span className="text-sm text-muted">{fmtDate(a.createdAt)}</span></p>
                <p className="mt-2"><Link className="font-semibold text-emerald-700" href={`/${lang}/admin/sellers/${a.seller.tamagneId}`}>{a.seller.displayName} ({a.seller.tamagneId})</Link> · account {a.seller.accountStatus.toLowerCase()}</p>
                {a.seller.suspensionReason ? <p className="text-sm text-muted">Suspension reason: {a.seller.suspensionReason}</p> : null}
                <p className="mt-2 whitespace-pre-wrap text-sm">{a.reason}</p>
                <div className="mt-3">
                  <ActionForm
                    path={`/admin/appeals/${a.id}/decision`}
                    submitLabel="Decide"
                    fields={[
                      { name: "outcome", label: "Outcome", type: "select", options: [{ value: "UPHELD", label: "Upheld (decision stands)" }, { value: "OVERTURNED", label: "Overturned (seller is right)" }] },
                      { name: "note", label: "Note to the seller", type: "textarea", required: true },
                    ]}
                  />
                </div>
              </Card>
            ))}
          </>
        );
      }}
    </AdminPage>
  );
}
