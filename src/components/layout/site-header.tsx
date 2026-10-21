import Link from "next/link";
import { Suspense } from "react";
import { siteConfig } from "@/config/site";
import { getViewer } from "@/lib/auth/session";
import { buttonClass, Container } from "../ui";
import { MobileNav } from "./mobile-nav";
import { ThemeToggle } from "./theme-toggle";

const NAV = [
  { href: "/markets", label: "Markets" },
  { href: "/research", label: "Research" },
  { href: "/pricing", label: "Pricing" },
  { href: "/about", label: "Methodology" },
];

async function AccountLinks() {
  const viewer = await getViewer();
  if (!viewer.user) {
    return (
      <>
        <Link href="/login" className={buttonClass("ghost", "sm")}>
          Sign in
        </Link>
        <span className="hidden sm:inline">
          <Link href="/register" className={buttonClass("primary", "sm")}>
            Create account
          </Link>
        </span>
      </>
    );
  }
  return (
    <>
      {viewer.isStaff ? (
        <span className="hidden sm:inline">
          <Link href="/admin" className={buttonClass("ghost", "sm")}>
            Admin
          </Link>
        </span>
      ) : null}
      <Link href="/dashboard" className={buttonClass("secondary", "sm")}>
        Dashboard
      </Link>
    </>
  );
}

export function SiteHeader() {
  return (
    <header className="sticky top-0 z-30 border-b border-line bg-bg/90 backdrop-blur">
      <Container className="relative flex h-14 items-center gap-4">
        <Link href="/" className="flex items-center gap-2 font-semibold tracking-tight">
          <span aria-hidden className="inline-block h-6 w-6 rounded-md bg-accent" />
          <span>{siteConfig.shortName}</span>
          <span className="hidden text-faint lg:inline">Research</span>
        </Link>
        <nav aria-label="Main" className="hidden md:block">
          <ul className="flex items-center gap-1">
            {NAV.map((n) => (
              <li key={n.href}>
                <Link href={n.href} className="rounded-md px-3 py-1.5 text-sm text-muted hover:bg-subtle hover:text-fg">
                  {n.label}
                </Link>
              </li>
            ))}
          </ul>
        </nav>
        <div className="ml-auto flex items-center gap-1.5">
          <ThemeToggle />
          <Suspense fallback={<span className="h-8 w-20" />}>
            <AccountLinks />
          </Suspense>
          <MobileNav links={[...NAV, { href: "/dashboard", label: "Dashboard" }]} />
        </div>
      </Container>
    </header>
  );
}
