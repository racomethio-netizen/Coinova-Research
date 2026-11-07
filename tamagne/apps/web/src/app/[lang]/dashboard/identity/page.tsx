import { notFound, redirect } from "next/navigation";
import { FaydaStartForm, ManualConsentButton, ManualReviewForm, WithdrawManualButton } from "@/components/identity-forms";
import { SiteHeader } from "@/components/site-header";
import { StatusPill } from "@/components/status-pill";
import { Alert, Card, CardTitle } from "@/components/ui/card";
import { getDictionary, hasLocale } from "@/i18n/dictionaries";
import { getFaydaStatus, getManualReview, getMySeller, getViewer, hasConsent } from "@/lib/server-api";

export default async function IdentityPage({ params, searchParams }: PageProps<"/[lang]/dashboard/identity">) {
  const { lang } = await params;
  if (!hasLocale(lang)) notFound();
  if (!(await getViewer())) redirect(`/${lang}/login`);
  const seller = await getMySeller();
  if (!seller) redirect(`/${lang}/dashboard/setup`);
  const [fayda, manual, manualConsent] = await Promise.all([getFaydaStatus(), getManualReview(), hasConsent("IDENTITY_DOCUMENT_REVIEW")]);
  const sp = await searchParams;
  const t = getDictionary(lang);
  const id = t.identity;
  const d = t.dashboard;
  const fv = seller.verification.faydaIdentity;
  const mv = seller.verification.identityManualReview;
  const reason = typeof sp.reason === "string" ? (id.reasons[sp.reason as keyof typeof id.reasons] ?? id.reasons.generic) : id.reasons.generic;
  const identityDone = (fv.status === "VERIFIED" && !fv.testMode) || mv.status === "VERIFIED";

  return (
    <>
      <SiteHeader lang={lang} t={t} path="/dashboard/identity" signedIn />
      <main className="mx-auto max-w-3xl space-y-6 px-4 py-10">
        <div>
          <h1 className="text-2xl font-bold">{id.title}</h1>
          <p className="mt-2 text-muted">{id.lead}</p>
        </div>

        {sp.fayda === "verified" ? (
          fv.testMode ? (
            <Alert tone="info" data-testid="fayda-result">{id.faydaVerifiedTest}</Alert>
          ) : (
            <Alert tone="success" data-testid="fayda-result">{id.faydaVerified}</Alert>
          )
        ) : null}
        {sp.fayda === "failed" ? <Alert tone="error" data-testid="fayda-result">{id.faydaFailed} {reason}</Alert> : null}

        <Card data-testid="fayda-card">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <CardTitle>{id.faydaTitle}</CardTitle>
            <span className="flex items-center gap-2">
              {fv.testMode ? <span className="rounded border border-amber-600/50 bg-amber-50 px-2 py-0.5 text-xs font-semibold text-amber-900">{id.testLabel}</span> : null}
              <StatusPill status={fv.status} label={d.statuses[fv.status]} />
            </span>
          </div>
          {!fayda.enabled ? (
            <p className="mt-3 text-sm" data-testid="fayda-unavailable">{id.faydaUnavailable}</p>
          ) : fv.status === "VERIFIED" ? null : (
            <div className="mt-3 space-y-4">
              <p className="text-sm text-muted">{id.faydaLead}</p>
              {fayda.mode === "sandbox" ? <Alert tone="info">{id.faydaSandbox}</Alert> : null}
              <FaydaStartForm t={id} lang={lang} />
            </div>
          )}
        </Card>

        <Card data-testid="manual-card">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <CardTitle>{id.manualTitle}</CardTitle>
            <StatusPill status={mv.status} label={d.statuses[mv.status]} />
          </div>
          <p className="mt-3 text-sm text-muted">{id.manualLead}</p>
          <p className="mt-1 text-xs text-muted">{id.manualRetention}</p>
          {manual && manual.status !== "WITHDRAWN" && !(manual.status === "APPROVED" && mv.status !== "VERIFIED") ? (
            <div className="mt-4 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-line p-3 text-sm" data-testid="manual-status">
              <span>
                {id.docTypes[manual.documentType]} · <strong>{id.statuses[manual.status]}</strong>
                {manual.status === "REJECTED" && manual.decisionReason ? <span className="block text-red-800">{id.rejected} {manual.decisionReason}</span> : null}
              </span>
              {manual.status === "SUBMITTED" ? <WithdrawManualButton t={id} /> : null}
            </div>
          ) : null}
          {!identityDone && manual?.status !== "SUBMITTED" ? (
            <div className="mt-4 space-y-4">
              {manualConsent ? (
                <ManualReviewForm t={id} />
              ) : (
                <>
                  <p className="text-sm">{id.manualConsent}</p>
                  <ManualConsentButton t={id} />
                </>
              )}
            </div>
          ) : null}
        </Card>
      </main>
    </>
  );
}
