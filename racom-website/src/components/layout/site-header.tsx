import Link from "next/link";
import { mainNav, primaryCta, site } from "@/config/site";
import { Logo } from "@/components/brand/logo";
import { ButtonLink } from "@/components/ui/button";
import { NavLinks } from "./nav-links";
import { MobileMenu } from "./mobile-menu";

export function SiteHeader() {
  return (
    <header className="sticky top-0 z-40 h-[var(--header-height)] border-b border-line bg-obsidian/75 backdrop-blur-xl supports-[backdrop-filter]:bg-obsidian/60">
      <div className="container-page flex h-full items-center justify-between gap-4">
        <Link href="/" className="relative z-[60] -m-1 rounded-lg p-1" aria-label={`${site.name} — home`}>
          <Logo />
        </Link>
        <nav aria-label="Main" className="hidden lg:block">
          <NavLinks items={mainNav} />
        </nav>
        <div className="flex items-center gap-3">
          <div className="hidden sm:block">
            <ButtonLink href={primaryCta.href} arrow>
              {primaryCta.label}
            </ButtonLink>
          </div>
          <MobileMenu items={mainNav} cta={primaryCta} />
        </div>
      </div>
    </header>
  );
}
