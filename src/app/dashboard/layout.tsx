import type { Metadata } from "next";
import Link from "next/link";
import { logoutAction } from "@/app/(auth)/actions";
import { Badge, Container } from "@/components/ui";
import { requireUserPage } from "@/lib/auth/session";
import { unreadCount } from "@/server/notifications";

export const metadata: Metadata = { title: "Dashboard", robots: { index: false } };
export const dynamic = "force-dynamic";

const LINKS = [
  { href: "/dashboard", label: "Overview" },
  { href: "/dashboard/portfolio", label: "Portfolio" },
  { href: "/dashboard/watchlist", label: "Watchlists" },
  { href: "/dashboard/alerts", label: "Alerts" },
  { href: "/dashboard/notifications", label: "Notifications" },
  { href: "/dashboard/assistant", label: "AI assistant" },
  { href: "/dashboard/membership", label: "Membership" },
  { href: "/dashboard/settings", label: "Settings" },
];

export default async function DashboardLayout({ children }: { children: React.ReactNode }) {
  const viewer = await requireUserPage();
  const unread = await unreadCount(viewer.supabase, viewer.user.id).catch(() => 0);
  return (
    <Container className="grid gap-8 py-8 lg:grid-cols-[220px_1fr]">
      <aside>
        <div className="mb-4 rounded-lg border border-line bg-elevated p-4">
          <p className="truncate text-sm font-medium" title={viewer.user.email ?? undefined}>
            {viewer.user.email}
          </p>
          <div className="mt-2 flex items-center gap-2">
            <Badge tone={viewer.tier === "free" ? "neutral" : "accent"}>{viewer.tier === "free" ? "Free" : viewer.tier === "premium" ? "Premium" : "Founding"}</Badge>
            {viewer.isAdmin ? <Badge tone="warning">Admin</Badge> : viewer.isStaff ? <Badge tone="warning">Editor</Badge> : null}
          </div>
        </div>
        <nav aria-label="Dashboard">
          <ul className="flex gap-1 overflow-x-auto pb-2 lg:flex-col lg:overflow-visible lg:pb-0">
            {LINKS.map((l) => (
              <li key={l.href} className="shrink-0">
                <Link href={l.href} className="flex items-center justify-between gap-2 rounded-md px-3 py-2 text-sm text-muted hover:bg-subtle hover:text-fg">
                  {l.label}
                  {l.href === "/dashboard/notifications" && unread > 0 ? (
                    <span className="rounded-full bg-accent px-1.5 text-xs font-semibold text-accent-fg" aria-label={`${unread} unread`}>
                      {unread}
                    </span>
                  ) : null}
                </Link>
              </li>
            ))}
            <li className="shrink-0">
              <form action={logoutAction}>
                <button type="submit" className="w-full rounded-md px-3 py-2 text-left text-sm text-muted hover:bg-subtle hover:text-fg">
                  Sign out
                </button>
              </form>
            </li>
          </ul>
        </nav>
      </aside>
      <div className="min-w-0">{children}</div>
    </Container>
  );
}
