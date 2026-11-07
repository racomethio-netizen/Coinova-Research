import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { ShieldMark } from "@/components/badge-icon";
import { SiteHeader } from "@/components/site-header";
import { StatusPill } from "@/components/status-pill";
import { Alert, Card } from "@/components/ui/card";
import { getDictionary, hasLocale } from "@/i18n/dictionaries";
import { lookupSeller, type PublicSeller } from "@/lib/server-api";

// Seller pages are public by consent, but are kept out of search engines.
export const metadata: Metadata = { robots: { index: false, follow: false } };

const ORDER = ["phone", "faydaIdentity", "identityManualReview", "business", "enhancedReview"] as const;

export default async function VerifyResultPage({ params }: PageProps<"/[lang]/verify/[id]">) {
  const { lang, id } = await params;
  if (!hasLocale(lang)) notFound();
  const t = getDictionary(lang);
  const v = t.verify;
  const d = t.dashboard;
  const outcome = await lookupSeller(decodeURIComponent(id).slice(0, 64));
  const fmt = new Intl.DateTimeFormat(lang === "am" ? "am-ET" : "en-GB", { dateStyle: "medium", timeZone: "Africa/Addis_Ababa" });
  const fmtTime = new Intl.DateTimeFormat(lang === "am" ? "am-ET" : "en-GB", { dateStyle: "medium", timeStyle: "short", timeZone: "Africa/Addis_Ababa" });

  return (
    <>
      <SiteHeader lang={lang} t={t} path={`/verify/${id}`} />
      <main className="mx-auto max-w-2xl space-y-6 px-4 py-10">
        {outcome.kind === "found" ? (
          <Result seller={outcome.seller} />
        ) : outcome.kind === "qr_revoked" ? (
          <Card className="border-red-700/40" data-testid="verify-revoked">
            <h1 className="text-xl font-bold text-red-800">{v.revokedTitle}</h1>
            <p className="mt-2">{v.revokedBody}</p>
          </Card>
        ) : outcome.kind === "not_found" ? (
          <Card data-testid="verify-not-found">
            <h1 className="text-xl font-bold">{v.notFoundTitle}</h1>
            <p className="mt-2 text-muted">{v.notFoundBody}</p>
          </Card>
        ) : (
          <Alert tone="error">{outcome.kind === "rate_limited" ? v.rateLimited : v.unavailable}</Alert>
        )}

        <Card className="bg-mist">
          <h2 className="font-semibold">{v.safetyTitle}</h2>
          <ul className="mt-2 list-disc space-y-1.5 ps-5 text-sm text-muted">
            {v.safety.map((s) => (
              <li key={s}>{s}</li>
            ))}
          </ul>
          <p className="mt-3 text-xs text-muted">{t.footer.independence}</p>
        </Card>

        <Link href={`/${lang}/verify`} className="inline-block font-semibold text-emerald-700 underline-offset-4 hover:underline">
          {v.anotherCheck}
        </Link>
      </main>
    </>
  );

  function Result({ seller }: { seller: PublicSeller }) {
    const primary = lang === "am" && seller.displayNameAm ? seller.displayNameAm : seller.displayName;
    const secondary = lang === "am" ? (seller.displayNameAm ? seller.displayName : null) : seller.displayNameAm;
    const rows = ORDER.filter((k) => k !== "identityManualReview" || seller.verification[k].status !== "NOT_VERIFIED");
    return (
      <Card data-testid="verify-result">
        <p className="text-sm font-medium text-muted">{v.resultTitle}</p>
        <div className="mt-2 flex items-start gap-3">
          <ShieldMark className="mt-1 size-8 shrink-0 text-emerald-700" />
          <div className="min-w-0">
            <h1 className="break-words text-2xl font-bold">{primary}</h1>
            {secondary ? <p className="break-words text-muted" lang={lang === "am" ? "en" : "am"}>{secondary}</p> : null}
          </div>
        </div>
        <dl className="mt-4 grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
          <div>
            <dt className="text-muted">{d.tamagneId}</dt>
            <dd className="font-mono text-base font-semibold" data-testid="verify-tamagne-id">{seller.tamagneId}</dd>
          </div>
          <div>
            <dt className="text-muted">{d.accountStatus}</dt>
            <dd className="text-base font-semibold" data-testid="verify-account-status">
              {d.accountStatuses[seller.accountStatus]}
            </dd>
          </div>
        </dl>
        {seller.accountStatus === "SUSPENDED" ? <Alert tone="error" className="mt-4">{d.accountStatuses.SUSPENDED}</Alert> : null}
        {seller.matchedBy === "qr_token" ? <Alert tone="success" className="mt-4">{v.qrValid}</Alert> : null}

        <ul className="mt-5 divide-y divide-line border-t border-line">
          {rows.map((key) => {
            const c = seller.verification[key];
            return (
              <li key={key} className="py-3" data-testid={`verify-${key}`}>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-medium">
                    {d.categories[key]}
                    {c.testMode ? <span className="ms-2 rounded border border-amber-600/50 bg-amber-50 px-1.5 py-0.5 text-xs font-semibold text-amber-900">{t.identity.testLabel}</span> : null}
                  </span>
                  <StatusPill status={c.status} label={d.statuses[c.status]} />
                </div>
                {c.status === "VERIFIED" && c.verifiedAt && c.expiresAt ? (
                  <p className="mt-1 text-sm text-muted">
                    {v.verifiedOn} {fmt.format(new Date(c.verifiedAt))} · {v.expires} {fmt.format(new Date(c.expiresAt))}
                  </p>
                ) : null}
              </li>
            );
          })}
        </ul>
        <p className="mt-4 text-sm font-medium">{v.matchTip}</p>
        <p className="mt-2 text-xs text-muted">
          {v.checkedAt}: {fmtTime.format(new Date(seller.checkedAt))}
        </p>
      </Card>
    );
  }
}
