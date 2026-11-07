import Link from "next/link";
import { ShieldMark } from "@/components/badge-icon";
import { SignOutButton } from "@/components/sign-out-button";
import type { Dictionary, Locale } from "@/i18n/dictionaries";

/** `path` is the current page without the locale prefix, so the language switch keeps the page. */
export function SiteHeader({ lang, t, path = "", signedIn = false }: { lang: Locale; t: Dictionary; path?: string; signedIn?: boolean }) {
  const other = lang === "en" ? "am" : "en";
  return (
    <header className="bg-navy-950 text-white">
      <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-3 px-4 py-4">
        <Link href={`/${lang}`} className="flex items-center gap-2 text-lg font-semibold tracking-wide">
          <ShieldMark className="size-7 text-emerald-600" />
          TAMAGNE
        </Link>
        <nav aria-label="Main" className="flex items-center gap-2 text-sm">
          {signedIn ? (
            <>
              <Link href={`/${lang}/dashboard`} className="rounded-md px-3 py-2 hover:bg-white/10">
                {t.nav.dashboard}
              </Link>
              <SignOutButton lang={lang} label={t.nav.signOut} />
            </>
          ) : (
            <>
              <Link href={`/${lang}/login`} className="rounded-md px-3 py-2 hover:bg-white/10">
                {t.nav.signIn}
              </Link>
              <Link href={`/${lang}/register`} className="rounded-md bg-emerald-700 px-3 py-2 font-semibold hover:bg-emerald-600">
                {t.nav.register}
              </Link>
            </>
          )}
          <Link
            href={`/${other}${path}`}
            hrefLang={other}
            lang={other}
            aria-label={t.nav.switchLanguageLabel}
            className="rounded-md border border-white/30 px-3 py-2 hover:bg-white/10"
          >
            {t.nav.switchLanguage}
          </Link>
        </nav>
      </div>
    </header>
  );
}
