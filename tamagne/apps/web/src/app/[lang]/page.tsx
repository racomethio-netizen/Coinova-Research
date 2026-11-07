import Link from "next/link";
import { notFound } from "next/navigation";
import { ShieldMark } from "@/components/badge-icon";
import { getDictionary, hasLocale } from "@/i18n/dictionaries";

export default async function Home({ params }: PageProps<"/[lang]">) {
  const { lang } = await params;
  if (!hasLocale(lang)) notFound();
  const t = getDictionary(lang);
  const other = lang === "en" ? "am" : "en";

  return (
    <>
      <header className="bg-navy-950 text-white">
        <div className="mx-auto flex max-w-5xl items-center justify-between gap-4 px-4 py-4">
          <span className="flex items-center gap-2 text-lg font-semibold tracking-wide">
            <ShieldMark className="size-7 text-emerald-600" />
            TAMAGNE
          </span>
          <Link
            href={`/${other}`}
            hrefLang={other}
            lang={other}
            aria-label={t.nav.switchLanguageLabel}
            className="rounded-md border border-white/30 px-3 py-1.5 text-sm hover:bg-white/10"
          >
            {t.nav.switchLanguage}
          </Link>
        </div>
      </header>

      <main>
        <section className="bg-navy-950 text-white">
          <div className="mx-auto max-w-5xl px-4 pb-16 pt-10 sm:pt-16">
            <h1 className="text-3xl font-bold leading-tight sm:text-5xl">{t.hero.tagline}</h1>
            <p className="mt-5 max-w-2xl text-base text-white/85 sm:text-lg">{t.hero.lead}</p>
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
        </div>
      </footer>
    </>
  );
}
