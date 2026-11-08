import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { ConsentToggle, CorrectionForm, DeleteAccountForm } from "@/components/privacy-forms";
import { SiteHeader } from "@/components/site-header";
import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import { getDictionary, hasLocale } from "@/i18n/dictionaries";
import { adminGet, getViewer } from "@/lib/server-api";

interface Consent { purpose: keyof ReturnType<typeof getDictionary>["privacy"]["purposes"]; status: "GRANTED" | "WITHDRAWN" | "NONE"; policyVersion: string | null; withdrawable: boolean; grantedAt: string | null }
interface Dsr { id: string; type: "ACCESS" | "CORRECTION" | "DELETION"; status: "RECEIVED" | "IN_PROGRESS" | "COMPLETED" | "REJECTED"; outcome: string | null; createdAt: string; dueAt: string }

export default async function PrivacyCenter({ params }: PageProps<"/[lang]/dashboard/privacy">) {
  const { lang } = await params;
  if (!hasLocale(lang)) notFound();
  if (!(await getViewer())) redirect(`/${lang}/login`);
  const t = getDictionary(lang);
  const p = t.privacy;
  const [consents, requests] = await Promise.all([adminGet<Consent[]>("/consents"), adminGet<Dsr[]>("/privacy/requests")]);
  const fmt = new Intl.DateTimeFormat(lang === "am" ? "am-ET" : "en-GB", { dateStyle: "medium", timeZone: "Africa/Addis_Ababa" });
  return (
    <>
      <SiteHeader lang={lang} t={t} path="/dashboard/privacy" signedIn />
      <main className="mx-auto max-w-3xl space-y-6 px-4 py-10">
        <div>
          <h1 className="text-2xl font-bold">{p.title}</h1>
          <p className="mt-2 text-muted">{p.lead}</p>
          <p className="mt-2 text-sm">
            <Link className="font-semibold text-emerald-700 underline" href={`/${lang}/privacy`}>{t.legal.privacy}</Link>
            {" · "}
            <Link className="font-semibold text-emerald-700 underline" href={`/${lang}/terms`}>{t.legal.terms}</Link>
          </p>
        </div>

        <Card>
          <CardTitle>{p.consentsTitle}</CardTitle>
          <ul className="mt-3 divide-y divide-line" data-testid="consents">
            {(consents.ok ? consents.data : []).map((c) => (
              <li key={c.purpose} className="flex flex-wrap items-center justify-between gap-2 py-3 text-sm">
                <span>
                  <strong>{p.purposes[c.purpose]}</strong> · {p.consentStatuses[c.status]}
                  {c.policyVersion ? <span className="text-muted"> ({p.version} {c.policyVersion})</span> : null}
                  {c.purpose === "FAYDA_IDENTITY_VERIFICATION" && c.status === "GRANTED" ? <span className="block text-xs text-muted">{p.faydaWithdrawNote}</span> : null}
                  {!c.withdrawable ? <span className="block text-xs text-muted">{p.requiredNote}</span> : null}
                </span>
                {c.withdrawable ? <ConsentToggle purpose={c.purpose} granted={c.status === "GRANTED"} t={p} /> : null}
              </li>
            ))}
          </ul>
        </Card>

        <Card>
          <CardTitle>{p.exportTitle}</CardTitle>
          <p className="mt-1 text-sm text-muted">{p.exportLead}</p>
          <Button asChild variant="outline" className="mt-3">
            <a href="/api/v1/privacy/export" download data-testid="export-link">{p.exportButton}</a>
          </Button>
        </Card>

        <Card>
          <CardTitle>{p.correctionTitle}</CardTitle>
          <p className="mt-1 text-sm text-muted">{p.correctionLead}</p>
          <div className="mt-3"><CorrectionForm t={p} /></div>
        </Card>

        <Card>
          <CardTitle>{p.requestsTitle}</CardTitle>
          <ul className="mt-2 divide-y divide-line text-sm" data-testid="privacy-requests">
            {(requests.ok ? requests.data : []).map((r) => (
              <li key={r.id} className="py-2">
                <strong>{p.requestTypes[r.type]}</strong> · {p.requestStatuses[r.status]} · {fmt.format(new Date(r.createdAt))}
                {r.outcome ? <span className="block text-muted">{r.outcome}</span> : null}
              </li>
            ))}
          </ul>
        </Card>

        <Card className="border-red-700/40">
          <CardTitle className="text-red-800">{p.deleteTitle}</CardTitle>
          <p className="mt-1 text-sm">{p.deleteLead}</p>
          <div className="mt-3"><DeleteAccountForm t={p} lang={lang} /></div>
        </Card>
      </main>
    </>
  );
}
