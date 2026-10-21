import type { Metadata } from "next";
import Link from "next/link";
import { Badge, Container } from "@/components/ui";
import { requireStaffPage } from "@/lib/auth/session";

export const metadata: Metadata = { title: "Admin", robots: { index: false } };
export const dynamic = "force-dynamic";

export default async function AdminLayout({ children }: { children: React.ReactNode }) {
  // Server-side role check from the database (user_roles), never a client flag.
  const viewer = await requireStaffPage();
  const links = [
    { href: "/admin", label: "Overview", admin: true },
    { href: "/admin/research", label: "Research", admin: false },
    { href: "/admin/coins", label: "Coins & unlocks", admin: false },
    { href: "/admin/scoring", label: "Scoring", admin: true },
    { href: "/admin/users", label: "Users", admin: true },
  ].filter((l) => !l.admin || viewer.isAdmin);
  return (
    <Container className="py-8">
      <div className="mb-6 flex flex-wrap items-center gap-3 border-b border-line pb-4">
        <Badge tone="warning">{viewer.isAdmin ? "Administrator" : "Editor"}</Badge>
        <nav aria-label="Admin">
          <ul className="flex flex-wrap gap-1">
            {links.map((l) => (
              <li key={l.href}>
                <Link href={l.href} className="rounded-md px-3 py-1.5 text-sm text-muted hover:bg-subtle hover:text-fg">
                  {l.label}
                </Link>
              </li>
            ))}
          </ul>
        </nav>
      </div>
      {children}
    </Container>
  );
}
