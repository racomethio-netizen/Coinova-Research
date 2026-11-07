import { notFound, redirect } from "next/navigation";
import {
  BusinessDetailsForm,
  ConsentButton,
  DocumentUploadForm,
  RemoveDocumentButton,
  SubmitBusinessButton,
} from "@/components/business-forms";
import { SiteHeader } from "@/components/site-header";
import { Alert, Card, CardTitle } from "@/components/ui/card";
import { getDictionary, hasLocale } from "@/i18n/dictionaries";
import { getMyBusiness, getMySeller, getViewer, hasConsent } from "@/lib/server-api";

export default async function BusinessPage({ params }: PageProps<"/[lang]/dashboard/business">) {
  const { lang } = await params;
  if (!hasLocale(lang)) notFound();
  if (!(await getViewer())) redirect(`/${lang}/login`);
  if (!(await getMySeller())) redirect(`/${lang}/dashboard/setup`);
  const [business, consented] = await Promise.all([getMyBusiness(), hasConsent("BUSINESS_DOCUMENT_PROCESSING")]);
  const t = getDictionary(lang);
  const b = t.business;
  const editable = !business || business.status === "DRAFT" || business.status === "REJECTED";
  const status = business?.status ?? "NONE";
  const fmtSize = (n: number) => (n > 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} MB` : `${Math.ceil(n / 1024)} KB`);

  return (
    <>
      <SiteHeader lang={lang} t={t} path="/dashboard/business" signedIn />
      <main className="mx-auto max-w-3xl space-y-6 px-4 py-10">
        <div>
          <h1 className="text-2xl font-bold">{b.title}</h1>
          <p className="mt-2 text-muted">{b.lead}</p>
          <p className="mt-1 text-sm text-muted">{b.notIdentity}</p>
          <p className="mt-3 text-sm">
            <span className="text-muted">{b.statusLabel}: </span>
            <strong data-testid="business-status">{b.statuses[status]}</strong>
          </p>
        </div>

        {status === "SUBMITTED" ? <Alert tone="info">{b.submittedNote}</Alert> : null}
        {status === "VERIFIED" ? <Alert tone="success">{b.verifiedNote}</Alert> : null}
        {status === "REJECTED" && business?.decisionReason ? (
          <Alert tone="error" data-testid="business-decision">{b.rejectedNote} {business.decisionReason}</Alert>
        ) : null}
        {status === "DRAFT" && business?.decisionReason ? (
          <Alert tone="error" data-testid="business-decision">{b.changesNote} {business.decisionReason}</Alert>
        ) : null}

        <Card>
          <CardTitle>{b.detailsTitle}</CardTitle>
          <div className="mt-4">
            {editable ? (
              <BusinessDetailsForm t={b} business={business} />
            ) : (
              <>
                <p className="text-sm text-muted">{b.lockedNote}</p>
                <dl className="mt-3 grid gap-2 text-sm sm:grid-cols-2">
                  <div>
                    <dt className="text-muted">{b.legalName}</dt>
                    <dd className="font-medium">{business!.legalName}</dd>
                  </div>
                  <div>
                    <dt className="text-muted">{b.legalForm}</dt>
                    <dd className="font-medium">{b.legalForms[business!.legalForm]}</dd>
                  </div>
                </dl>
              </>
            )}
          </div>
        </Card>

        {business ? (
          <>
            <Card>
              <CardTitle>{b.consentTitle}</CardTitle>
              <p className="mt-2 text-sm">{b.consentText}</p>
              <div className="mt-4">{consented ? <p className="text-sm font-medium text-emerald-700" data-testid="consent-given">✓ {b.consentGiven}</p> : <ConsentButton t={b} />}</div>
            </Card>

            <Card>
              <CardTitle>{b.documentsTitle}</CardTitle>
              <p className="mt-1 text-sm text-muted">{b.required}</p>
              {business.documents.length === 0 ? (
                <p className="mt-4 text-sm">{b.none}</p>
              ) : (
                <ul className="mt-4 divide-y divide-line border-y border-line" data-testid="documents">
                  {business.documents.map((d) => (
                    <li key={d.id} className="flex flex-wrap items-center justify-between gap-2 py-3">
                      <div className="min-w-0">
                        <p className="font-medium">{b.docTypes[d.type]}</p>
                        <p className="text-sm text-muted">
                          {d.mimeType === "application/pdf" ? "PDF" : d.mimeType === "image/png" ? "PNG" : "JPEG"} · {fmtSize(d.sizeBytes)} · {b.scan[d.scanStatus]} · {b.review[d.reviewStatus]}
                        </p>
                        {d.rejectionReason ? <p className="text-sm text-red-800">{d.rejectionReason}</p> : null}
                      </div>
                      {editable ? <RemoveDocumentButton t={b} id={d.id} /> : null}
                    </li>
                  ))}
                </ul>
              )}
              {editable && consented ? (
                <div className="mt-5">
                  <DocumentUploadForm t={b} />
                </div>
              ) : null}
            </Card>

            {editable && consented && business.documents.length > 0 ? <SubmitBusinessButton t={b} /> : null}
          </>
        ) : null}
      </main>
    </>
  );
}
