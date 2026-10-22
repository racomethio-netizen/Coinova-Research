import Link from "next/link";
import type { ReactNode } from "react";
import { Eyebrow } from "./section";

/** Header block for inner pages: blueprint backdrop, eyebrow, H1 and lead text. */
export function PageHero({
  eyebrow,
  title,
  description,
  children,
}: {
  eyebrow: string;
  title: ReactNode;
  description?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <section className="relative isolate overflow-hidden border-b border-line">
      <div aria-hidden="true" className="bg-blueprint absolute inset-0 -z-10" />
      <div
        aria-hidden="true"
        className="absolute -top-56 right-[-10%] -z-10 h-[34rem] w-[44rem] rounded-full bg-[radial-gradient(closest-side,rgb(117_101_255/0.16),transparent)]"
      />
      <div className="container-page pt-16 pb-14 sm:pt-24 sm:pb-20">
        <Eyebrow className="animate-fade-up">{eyebrow}</Eyebrow>
        <h1 className="mt-5 max-w-4xl animate-rise font-display text-[2.4rem] leading-[1.02] font-semibold tracking-[-0.035em] text-balance text-ink [animation-delay:60ms] sm:text-6xl">
          {title}
        </h1>
        {description && (
          <p className="mt-6 max-w-2xl animate-rise text-lg leading-relaxed text-pretty text-muted [animation-delay:120ms]">
            {description}
          </p>
        )}
        {children && <div className="mt-8 animate-fade-up [animation-delay:180ms]">{children}</div>}
      </div>
    </section>
  );
}

/** Simple breadcrumb trail for detail pages. */
export function Breadcrumbs({ items }: { items: { label: string; href?: string }[] }) {
  return (
    <nav aria-label="Breadcrumb" className="mb-6 animate-fade-up">
      <ol className="flex flex-wrap items-center gap-2 text-sm text-subtle">
        {items.map((item, i) => (
          <li key={item.label} className="flex items-center gap-2">
            {i > 0 && <span aria-hidden="true">/</span>}
            {item.href ? (
              <Link href={item.href} className="inline-block py-1 hover:text-ink">
                {item.label}
              </Link>
            ) : (
              <span aria-current="page" className="text-muted">
                {item.label}
              </span>
            )}
          </li>
        ))}
      </ol>
    </nav>
  );
}
