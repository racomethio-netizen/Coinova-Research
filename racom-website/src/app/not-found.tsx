import type { Metadata } from "next";
import Link from "next/link";
import { mainNav } from "@/config/site";
import { ButtonLink } from "@/components/ui/button";
import { Eyebrow } from "@/components/ui/section";

export const metadata: Metadata = { title: "Page not found", robots: { index: false } };

export default function NotFound() {
  return (
    <section className="relative isolate overflow-hidden">
      <div aria-hidden="true" className="bg-blueprint absolute inset-0 -z-10" />
      <div className="container-page flex min-h-[70vh] flex-col justify-center py-20">
        <Eyebrow>Error 404</Eyebrow>
        <h1 className="mt-5 max-w-2xl font-display text-5xl leading-[1.02] font-semibold tracking-[-0.035em] text-ink sm:text-7xl">
          This page isn’t on the map.
        </h1>
        <p className="mt-6 max-w-xl text-lg leading-relaxed text-muted">
          The link may be outdated or the address mistyped. Here are some places to continue from:
        </p>
        <div className="mt-9 flex flex-col gap-3 min-[400px]:flex-row">
          <ButtonLink href="/" size="lg" arrow>
            Back to home
          </ButtonLink>
          <ButtonLink href="/contact" size="lg" variant="secondary">
            Start a Project
          </ButtonLink>
        </div>
        <nav aria-label="Popular pages" className="mt-14 border-t border-line pt-6">
          <ul className="flex flex-wrap gap-x-6 gap-y-2 text-sm">
            {mainNav.map((n) => (
              <li key={n.href}>
                <Link href={n.href} className="inline-block py-1 text-muted hover:text-ink">
                  {n.label}
                </Link>
              </li>
            ))}
          </ul>
        </nav>
      </div>
    </section>
  );
}
