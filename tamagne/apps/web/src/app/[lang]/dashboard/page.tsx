import { notFound, redirect } from "next/navigation";
import { PublicToggle } from "@/components/public-toggle";
import { SiteHeader } from "@/components/site-header";
import { StatusPill } from "@/components/status-pill";
import { Alert, Card, CardTitle } from "@/components/ui/card";
import { getDictionary, hasLocale } from "@/i18n/dictionaries";
import { getMySeller, getViewer, type SellerMe } from "@/lib/server-api";

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
                  <span className="font-medium">{d.categories[key]}</span>
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
              <li>{d.faydaPending}</li>
              <li>{d.businessPending}</li>
            </ul>
          </Card>
        </div>
      </main>
    </>
  );
}
