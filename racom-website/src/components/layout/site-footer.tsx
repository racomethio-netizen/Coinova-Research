import Link from "next/link";
import { legalNav, mainNav, primaryCta, site } from "@/config/site";
import { services } from "@/content/services";
import { products } from "@/content/products";
import { Logo } from "@/components/brand/logo";

function FooterColumn({ title, links }: { title: string; links: { label: string; href: string }[] }) {
  return (
    <div>
      <h2 className="font-mono text-[0.7rem] tracking-[0.18em] text-subtle uppercase">{title}</h2>
      <ul className="mt-4 space-y-1">
        {links.map((link) => (
          <li key={link.href + link.label}>
            <Link
              href={link.href}
              className="inline-block py-1.5 text-sm text-muted transition-colors hover:text-ink"
            >
              {link.label}
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function SiteFooter() {
  const year = new Date().getFullYear();
  const { email, phone, location } = site.contact;
  return (
    <footer className="relative border-t border-line bg-obsidian">
      <div className="container-page grid gap-12 py-16 md:grid-cols-12">
        <div className="md:col-span-4">
          <Link href="/" className="-m-1 inline-block rounded-lg p-1" aria-label={`${site.name} — home`}>
            <Logo />
          </Link>
          <p className="mt-5 max-w-xs text-sm leading-relaxed text-muted">{site.message}</p>
          <p className="mt-4 font-display text-sm font-medium text-violet-soft">{site.slogan}</p>
          <div className="mt-6 space-y-1.5 text-sm text-muted">
            {email && (
              <p>
                <a className="hover:text-ink" href={`mailto:${email}`}>
                  {email}
                </a>
              </p>
            )}
            {phone && (
              <p>
                <a className="hover:text-ink" href={`tel:${phone.replace(/\s+/g, "")}`}>
                  {phone}
                </a>
              </p>
            )}
            {location && <p>{location}</p>}
            {!email && !phone && (
              <p>
                <Link className="inline-block py-1 text-violet-soft hover:text-ink" href={primaryCta.href}>
                  Send us a project inquiry →
                </Link>
              </p>
            )}
          </div>
          {site.social.length > 0 && (
            <ul className="mt-6 flex flex-wrap gap-4" aria-label="Social profiles">
              {site.social.map((s) => (
                <li key={s.href}>
                  <a
                    href={s.href}
                    rel="noopener noreferrer"
                    target="_blank"
                    className="text-sm text-muted hover:text-ink"
                  >
                    {s.label}
                  </a>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="grid grid-cols-2 gap-8 sm:grid-cols-3 md:col-span-8">
          <FooterColumn title="Company" links={[{ label: "Home", href: "/" }, ...mainNav]} />
          <FooterColumn
            title="Services"
            links={services.map((s) => ({ label: s.title, href: `/services/${s.slug}` }))}
          />
          <FooterColumn
            title="Products"
            links={[
              { label: "All products", href: "/products" },
              ...products.map((p) => ({ label: p.name, href: `/products#${p.slug}` })),
            ]}
          />
        </div>
      </div>
      <div className="border-t border-line">
        <div className="container-page flex flex-col gap-3 py-6 text-xs text-subtle sm:flex-row sm:items-center sm:justify-between">
          <p>
            © {year} {site.name}. All rights reserved.
          </p>
          <ul className="flex gap-5">
            {legalNav.map((l) => (
              <li key={l.href}>
                <Link href={l.href} className="inline-block py-1 hover:text-ink">
                  {l.label}
                </Link>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </footer>
  );
}
