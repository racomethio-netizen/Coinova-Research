import { notFound } from "next/navigation";
import { BusinessDecisionForm } from "@/components/admin/business-decision";
import { AdminPage, ApiErrorCard, Pill, fmtDate } from "@/components/admin/shell";
import { Alert, Card } from "@/components/ui/card";
import { hasLocale } from "@/i18n/dictionaries";
import { adminGet } from "@/lib/server-api";

export interface BusinessDetail {
  id: string;
  legalName: string;
  tradeName: string | null;
  legalForm: string;
  registrationNumber: string | null;
  tinNumber: string | null;
  region: string | null;
  city: string | null;
  status: string;
  submittedAt: string | null;
  seller: { tamagneId: string; displayName: string; displayNameAm: string | null; accountStatus: string };
  documents: { id: string; type: string; mimeType: string; sizeBytes: number; sha256: string; scanStatus: string; scanEngine: string | null; reviewStatus: string; rejectionReason: string | null; uploadedAt: string }[];
  reviews: { id: string; decision: string; reason: string; riskLevel: string; status: string; reviewerId: string; createdAt: string }[];
}

export default async function BusinessReview({ params }: PageProps<"/[lang]/admin/reviews/businesses/[id]">) {
  const { lang, id } = await params;
  if (!hasLocale(lang)) notFound();
  return (
    <AdminPage lang={lang} active="/reviews/businesses" roles={["SUPER_ADMIN", "VERIFICATION_OFFICER"]} title="Business review">
      {async () => {
        const r = await adminGet<BusinessDetail>(`/admin/businesses/${encodeURIComponent(id)}`);
        if (!r.ok) return <ApiErrorCard message={r.message} />;
        const b = r.data;
        const row = (k: string, v: string | null) => (
          <div><dt className="text-muted">{k}</dt><dd className="font-medium">{v ?? "—"}</dd></div>
        );
        const pendingSecond = b.reviews.some((x) => x.status === "PENDING_SECOND_REVIEW");
        const banner =
          b.status === "VERIFIED" ? { tone: "success" as const, text: "Approved. The Level 3 business badge has been issued." }
          : b.status === "REJECTED" ? { tone: "error" as const, text: "Rejected. The seller has been told the reason and can appeal." }
          : b.status === "DRAFT" ? { tone: "info" as const, text: "Changes requested. The business is back with the seller." }
          : pendingSecond ? { tone: "info" as const, text: "High-risk decision recorded. Waiting for a second reviewer." }
          : null;
        return (
          <>
            {banner ? <Alert tone={banner.tone} data-testid="review-status">{banner.text}</Alert> : null}
            <Card>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h2 className="text-lg font-semibold">{b.legalName}</h2>
                <Pill tone={b.status === "VERIFIED" ? "good" : b.status === "REJECTED" ? "bad" : "neutral"}>{b.status}</Pill>
              </div>
              <dl className="mt-3 grid gap-3 text-sm sm:grid-cols-3">
                {row("Trade name", b.tradeName)}
                {row("Legal form", b.legalForm.replace(/_/g, " ").toLowerCase())}
                {row("Registration no.", b.registrationNumber)}
                {row("TIN", b.tinNumber)}
                {row("Location", [b.city, b.region].filter(Boolean).join(", ") || null)}
                {row("Submitted", fmtDate(b.submittedAt))}
                {row("Seller", `${b.seller.displayName} (${b.seller.tamagneId})`)}
                {row("Seller account", b.seller.accountStatus)}
              </dl>
            </Card>
            <Card>
              <h2 className="font-semibold">Documents</h2>
              <p className="mt-1 text-xs text-muted">Downloads are logged. Files open as downloads only; they never render inside TAMAGNE.</p>
              <ul className="mt-3 divide-y divide-line">
                {b.documents.map((d) => (
                  <li key={d.id} className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm">
                    <span>
                      <strong>{d.type.replace(/_/g, " ").toLowerCase()}</strong> · {d.mimeType} · {Math.ceil(d.sizeBytes / 1024)} KB ·{" "}
                      <Pill tone={d.scanStatus === "CLEAN" ? "good" : "bad"}>scan {d.scanStatus.toLowerCase()}</Pill>{" "}
                      <Pill>{d.reviewStatus.toLowerCase()}</Pill>
                      <span className="block font-mono text-xs text-muted">sha256 {d.sha256.slice(0, 16)}…</span>
                    </span>
                    {d.scanStatus === "CLEAN" ? (
                      <a className="font-semibold text-emerald-700" href={`/api/v1/admin/documents/${d.id}/file`} download>Download</a>
                    ) : null}
                  </li>
                ))}
              </ul>
            </Card>
            {b.status === "SUBMITTED" && !pendingSecond ? (
              <Card>
                <h2 className="font-semibold">Decision</h2>
                <BusinessDecisionForm businessId={b.id} documents={b.documents.map((d) => ({ id: d.id, type: d.type, clean: d.scanStatus === "CLEAN" }))} />
              </Card>
            ) : null}
            {b.reviews.length ? (
              <Card>
                <h2 className="font-semibold">Review history</h2>
                <ul className="mt-2 space-y-2 text-sm">
                  {b.reviews.map((x) => (
                    <li key={x.id}><Pill>{x.decision}</Pill> <Pill tone={x.riskLevel === "HIGH" ? "warn" : "neutral"}>{x.riskLevel} risk</Pill> {x.status === "PENDING_SECOND_REVIEW" ? <Pill tone="warn">awaiting second reviewer</Pill> : null} {x.reason} <span className="text-muted">· {fmtDate(x.createdAt)}</span></li>
                  ))}
                </ul>
              </Card>
            ) : null}
          </>
        );
      }}
    </AdminPage>
  );
}
