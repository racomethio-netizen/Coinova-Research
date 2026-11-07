import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { PublicToggle } from "@/components/public-toggle";
import { QrCard } from "@/components/qr-card";
import { SiteHeader } from "@/components/site-header";
import { StatusPill } from "@/components/status-pill";
import { Alert, Card, CardTitle } from "@/components/ui/card";
import { getDictionary, hasLocale } from "@/i18n/dictionaries";
import { getMyQr, getMySeller, getViewer, type SellerMe } from "@/lib/server-api";

const ORDER = ["phone", "faydaIdentity", "identityManualReview", "business", "enhancedReview"] as const;

export default async function DashboardPage({ params }: PageProps<"/[lang]/dashboard">) {
  const { lang } = await params;
  if (!hasLocale(lang)) notFound();
  const viewer = await getViewer();
  if (!viewer) redirect(`/${lang}/login`);
  const seller = await getMySeller();
  if (!seller) redirect(`/${lang}/dashboard/setup`);
  const t = getDictionary(lang);
  const d = t.dashboard;
  const qr = seller.isPublic ? await getMyQr() : null;
  const fmt = new Intl.DateTimeFormat(lang === "am" ? "am-ET" : "en-GB", { dateStyle: "medium", timeZone: "Africa/Addis_Ababa" });
  const name = lang === "am" && seller.displayNameAm ? seller.displayNameAm : seller.displayName;

  // The manual-review row is only shown once it has a status, to avoid implying it is a ladder level.
  const rows = ORDER.filter((k) => k !== "identityManualReview" || seller.verification[k].status !== "NOT_VERIFIED");

  return (
    <>
      <SiteHeader lang={lang} t={t} path="/dashboard" signedIn />
      <main className="mx-auto max-w-5xl space-y-6 px-4 py-10">
        <div>
          <h1 className="text-2xl font-bold">{name}</h1>
          <dl className="mt-3 flex flex-wrap gap-x-8 gap-y-2 text-sm">
            <div>
              <dt className="text-muted">{d.tamagneId}</dt>
              <dd className="font-mono text-base font-semibold" data-testid="tamagne-id">{seller.tamagneId}</dd>
            </div>
            <div>
              <dt className="text-muted">{d.accountStatus}</dt>
              <dd className="text-base font-semibold">{d.accountStatuses[seller.accountStatus]}</dd>
            </div>
          </dl>
        </div>

        {seller.accountStatus === "SUSPENDED" ? <Alert tone="error">{d.suspended}</Alert> : null}

        <Card>
          <CardTitle>{d.title}</CardTitle>
          <ul className="mt-4 divide-y divide-line">
            {rows.map((key) => {
              const c = seller.verification[key as keyof SellerMe["verification"]];
              return (
                <li key={key} className="flex flex-wrap items-center justify-between gap-2 py-3" data-testid={`verification-${key}`}>
                  <span className="font-medium">
                    {d.categories[key]}
                    {c.testMode ? <span className="ms-2 rounded border border-amber-600/50 bg-amber-50 px-1.5 py-0.5 text-xs font-semibold text-amber-900">{t.identity.testLabel}</span> : null}
                  </span>
                  <span className="flex flex-wrap items-center gap-3 text-sm text-muted">
                    {c.status === "VERIFIED" && c.expiresAt ? (
                      <span>
                        {d.validUntil} {fmt.format(new Date(c.expiresAt))}
                      </span>
                    ) : null}
                    <StatusPill status={c.status} label={d.statuses[c.status]} />
                  </span>
                </li>
              );
            })}
          </ul>
        </Card>

        <Card data-testid="qr-card">
          <CardTitle>{t.qr.title}</CardTitle>
          <p className="mt-1 text-sm text-muted">{seller.isPublic ? t.qr.lead : t.qr.needsPublic}</p>
          {qr ? <div className="mt-4"><QrCard url={qr.url} svg={qr.svg} t={t.qr} /></div> : null}
        </Card>

        <div className="grid gap-6 md:grid-cols-2">
          <Card>
            <CardTitle>{d.publicProfile}</CardTitle>
            <p className="mt-2 text-sm text-muted" data-testid="public-state">{seller.isPublic ? d.publicOn : d.publicOff}</p>
            <div className="mt-4">
              <PublicToggle isPublic={seller.isPublic} labels={{ makePublic: d.makePublic, makePrivate: d.makePrivate }} />
            </div>
          </Card>
          <Card>
            <CardTitle>{d.nextSteps}</CardTitle>
            <ul className="mt-2 list-disc space-y-2 ps-5 text-sm text-muted">
              <li>
                <Link href={`/${lang}/dashboard/identity`} className="font-semibold text-emerald-700 underline-offset-4 hover:underline">
                  {t.identity.manage}
                </Link>{" "}
                ({t.identity.lead})
              </li>
              <li>
                <Link href={`/${lang}/dashboard/business`} className="font-semibold text-emerald-700 underline-offset-4 hover:underline">
                  {t.business.manage}
                </Link>{" "}
                ({t.business.lead})
              </li>
            </ul>
          </Card>
        </div>
      </main>
    </>
  );
}
