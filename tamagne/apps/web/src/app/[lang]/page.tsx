import Link from "next/link";
import { SiteHeader } from "@/components/site-header";
import { VerifyLookupForm } from "@/components/verify-lookup-form";
import { notFound } from "next/navigation";
import { getDictionary, hasLocale } from "@/i18n/dictionaries";

export default async function Home({ params }: PageProps<"/[lang]">) {
  const { lang } = await params;
  if (!hasLocale(lang)) notFound();
  const t = getDictionary(lang);

  return (
    <>
      <SiteHeader lang={lang} t={t} />

      <main>
        <section className="bg-navy-950 text-white">
          <div className="mx-auto max-w-5xl px-4 pb-16 pt-10 sm:pt-16">
            <h1 className="text-3xl font-bold leading-tight sm:text-5xl">{t.hero.tagline}</h1>
            <p className="mt-5 max-w-2xl text-base text-white/85 sm:text-lg">{t.hero.lead}</p>
            <div className="mt-8 max-w-xl rounded-xl border border-white/15 bg-navy-900 p-4 sm:p-5">
              <h2 className="mb-3 text-lg font-semibold">{t.verify.title}</h2>
              <VerifyLookupForm lang={lang} t={t.verify} tone="dark" />
            </div>
            <div className="mt-8 flex flex-wrap gap-3">
              <Link href={`/${lang}/register`} className="inline-flex min-h-12 items-center rounded-md border border-emerald-600 px-6 font-semibold hover:bg-white/10">
                {t.nav.register}
              </Link>
              <Link href={`/${lang}/login`} className="inline-flex min-h-12 items-center rounded-md border border-white/40 px-6 font-semibold hover:bg-white/10">
                {t.nav.signIn}
              </Link>
            </div>
            <p role="status" className="mt-8 inline-block rounded-lg border border-emerald-600/60 bg-navy-800 px-4 py-3 text-sm">
              <strong className="font-semibold">{t.status.heading}:</strong> {t.status.body}
            </p>
          </div>
        </section>

        <section aria-labelledby="levels" className="mx-auto max-w-5xl px-4 py-12">
          <h2 id="levels" className="text-2xl font-semibold">{t.levels.heading}</h2>
          <p className="mt-2 max-w-3xl text-muted">{t.levels.intro}</p>
          <ol className="mt-6 grid gap-4 sm:grid-cols-2">
            {t.levels.items.map((item) => (
              <li key={item.n} className="rounded-xl border border-line bg-white p-5 shadow-sm">
                <div className="flex items-center gap-3">
                  <span className="grid size-9 shrink-0 place-items-center rounded-full bg-emerald-100 font-semibold text-emerald-700" aria-hidden="true">
                    {item.n}
                  </span>
                  <h3 className="font-semibold">{item.title}</h3>
                </div>
                <p className="mt-3 text-sm text-muted">{item.body}</p>
              </li>
            ))}
          </ol>
          <p className="mt-6 rounded-lg border-l-4 border-emerald-600 bg-mist p-4 text-sm">{t.levels.manualNote}</p>
        </section>

        <section aria-labelledby="limits" className="bg-mist">
          <div className="mx-auto max-w-5xl px-4 py-12">
            <h2 id="limits" className="text-2xl font-semibold">{t.limits.heading}</h2>
            <ul className="mt-4 list-disc space-y-2 ps-5 text-muted">
              {t.limits.items.map((s) => (
                <li key={s}>{s}</li>
              ))}
            </ul>
          </div>
        </section>
      </main>

      <footer className="border-t border-line">
        <div className="mx-auto max-w-5xl space-y-2 px-4 py-8 text-sm text-muted">
          <p>{t.footer.independence}</p>
          <p>{t.footer.privacy}</p>
          <p className="flex gap-4">
            <Link href={`/${lang}/privacy`} className="underline-offset-4 hover:underline">{t.legal.privacy}</Link>
            <Link href={`/${lang}/terms`} className="underline-offset-4 hover:underline">{t.legal.terms}</Link>
          </p>
        </div>
      </footer>
    </>
  );
}
