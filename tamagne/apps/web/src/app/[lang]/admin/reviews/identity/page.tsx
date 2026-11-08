import { notFound } from "next/navigation";
import { ActionForm } from "@/components/admin/action-form";
import { AdminPage, ApiErrorCard, Pill, fmtDate } from "@/components/admin/shell";
import { Card } from "@/components/ui/card";
import { hasLocale } from "@/i18n/dictionaries";
import { adminGet } from "@/lib/server-api";

interface Item {
  id: string;
  documentType: string;
  submittedAt: string;
  seller: { tamagneId: string; displayName: string };
  secondReviewId: string | null;
}

export default async function IdentityQueue({ params }: PageProps<"/[lang]/admin/reviews/identity">) {
  const { lang } = await params;
  if (!hasLocale(lang)) notFound();
  return (
    <AdminPage lang={lang} active="/reviews/identity" roles={["SUPER_ADMIN", "VERIFICATION_OFFICER"]} title="Manual identity reviews">
      {async () => {
        const r = await adminGet<Item[]>("/admin/reviews/identity");
        if (!r.ok) return <ApiErrorCard message={r.message} />;
        return (
          <>
            <Card className="bg-white">
              <p className="text-sm">Approving here issues <strong>&ldquo;Identity reviewed manually&rdquo;</strong>. It is never shown as Fayda verification. Do not record ID numbers anywhere.</p>
            </Card>
            {r.data.length === 0 ? <Card><p className="text-sm text-muted">No identity documents are waiting.</p></Card> : null}
            {r.data.map((i) => (
              <Card key={i.id} data-testid="identity-item">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p>
                    <strong>{i.seller.displayName}</strong> <span className="font-mono text-sm">{i.seller.tamagneId}</span> · {i.documentType.replace(/_/g, " ").toLowerCase()} · {fmtDate(i.submittedAt)}
                  </p>
                  <a className="font-semibold text-emerald-700" href={`/api/v1/admin/identity-reviews/${i.id}/file`} download>Download document</a>
                </div>
                {i.secondReviewId ? (
                  <p className="mt-3 text-sm"><Pill tone="warn">awaiting second reviewer</Pill> — see Second reviews.</p>
                ) : (
                  <div className="mt-3">
                    <ActionForm
                      path={`/admin/identity-reviews/${i.id}/decision`}
                      submitLabel="Record decision"
                      fields={[
                        { name: "decision", label: "Decision", type: "select", options: [{ value: "APPROVE", label: "Approve" }, { value: "REJECT", label: "Reject" }] },
                        { name: "riskLevel", label: "Risk", type: "select", options: [{ value: "LOW", label: "Low" }, { value: "MEDIUM", label: "Medium" }, { value: "HIGH", label: "High (needs a second reviewer)" }] },
                        { name: "reason", label: "Reason (shown to the seller if rejected)", type: "textarea", required: true },
                      ]}
                    />
                  </div>
                )}
              </Card>
            ))}
          </>
        );
      }}
    </AdminPage>
  );
}
