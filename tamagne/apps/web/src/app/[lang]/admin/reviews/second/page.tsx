import { notFound } from "next/navigation";
import { ActionForm } from "@/components/admin/action-form";
import { AdminPage, ApiErrorCard, Pill, fmtDate } from "@/components/admin/shell";
import { Card } from "@/components/ui/card";
import { hasLocale } from "@/i18n/dictionaries";
import { adminGet } from "@/lib/server-api";

interface Pending {
  id: string;
  targetType: string;
  decision: string;
  reason: string;
  riskLevel: string;
  reviewerId: string;
  createdAt: string;
  seller: { tamagneId: string; displayName: string };
}

const WHAT: Record<string, string> = { BUSINESS: "Business verification", IDENTITY_MANUAL: "Manual identity review", ACCOUNT_STATUS: "Account reactivation" };

export default async function SecondReviews({ params }: PageProps<"/[lang]/admin/reviews/second">) {
  const { lang } = await params;
  if (!hasLocale(lang)) notFound();
  return (
    <AdminPage lang={lang} active="/reviews/second" roles={["SUPER_ADMIN", "VERIFICATION_OFFICER", "SECURITY_OFFICER"]} title="Second reviews">
      {async (viewer) => {
        const r = await adminGet<Pending[]>("/admin/reviews/second");
        if (!r.ok) return <ApiErrorCard message={r.message} />;
        if (r.data.length === 0) return <Card><p className="text-sm text-muted">Nothing is waiting for a second reviewer.</p></Card>;
        return r.data.map((p) => (
          <Card key={p.id} data-testid="second-review-item">
            <p className="text-sm text-muted">{WHAT[p.targetType] ?? p.targetType} · {fmtDate(p.createdAt)}</p>
            <p className="mt-1"><strong>{p.seller.displayName}</strong> <span className="font-mono text-sm">{p.seller.tamagneId}</span></p>
            <p className="mt-2 text-sm">First reviewer proposed <Pill>{p.decision}</Pill> <Pill tone="warn">{p.riskLevel} risk</Pill>: {p.reason}</p>
            {p.reviewerId === viewer.userId ? (
              <p className="mt-3 text-sm text-amber-900">You made the first decision, so a different reviewer must confirm it.</p>
            ) : (
              <div className="mt-3">
                <ActionForm
                  path={`/admin/reviews/${p.id}/second-review`}
                  submitLabel="Record second decision"
                  fields={[
                    { name: "decision", label: "Your decision", type: "select", options: [{ value: "APPROVE", label: "Agree / approve" }, { value: "REJECT", label: "Disagree / reject" }] },
                    { name: "reason", label: "Reason", type: "textarea", required: true },
                  ]}
                />
              </div>
            )}
          </Card>
        ));
      }}
    </AdminPage>
  );
}
