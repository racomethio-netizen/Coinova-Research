import Link from "next/link";
import { notFound } from "next/navigation";
import { AdminPage, ApiErrorCard, NAV, can } from "@/components/admin/shell";
import { Card } from "@/components/ui/card";
import { hasLocale } from "@/i18n/dictionaries";
import { adminGet } from "@/lib/server-api";

interface Overview {
  queues: { businesses: number; identity: number; secondReviews: number; reports: number; appeals: number };
  sellers: Record<string, number>;
  badges: { expiringIn30Days: number; lapsedNotYetExpired: number };
}

export default async function AdminHome({ params }: PageProps<"/[lang]/admin">) {
  const { lang } = await params;
  if (!hasLocale(lang)) notFound();
  return (
    <AdminPage lang={lang} active="" title="Overview">
      {async (viewer) => {
        const r = await adminGet<Overview>("/admin/overview");
        if (!r.ok) return <ApiErrorCard message={r.message} />;
        const o = r.data;
        const tiles = [
          { label: "Business reviews waiting", value: o.queues.businesses, href: "/reviews/businesses" },
          { label: "Identity reviews waiting", value: o.queues.identity, href: "/reviews/identity" },
          { label: "Second reviews waiting", value: o.queues.secondReviews, href: "/reviews/second" },
          { label: "Open fraud reports", value: o.queues.reports, href: "/reports" },
          { label: "Open appeals", value: o.queues.appeals, href: "/appeals" },
          { label: "Badges expiring in 30 days", value: o.badges.expiringIn30Days, href: "/expiry" },
        ].filter((t) => can(viewer, NAV.find((n) => n.href === t.href)!.roles));
        return (
          <>
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3" data-testid="overview">
              {tiles.map((t) => (
                <Link key={t.href} href={`/${lang}/admin${t.href}`}>
                  <Card className="hover:border-emerald-600">
                    <p className="text-sm text-muted">{t.label}</p>
                    <p className="mt-1 text-3xl font-bold">{t.value}</p>
                  </Card>
                </Link>
              ))}
            </div>
            <Card>
              <h2 className="font-semibold">Sellers by account status</h2>
              <dl className="mt-2 flex flex-wrap gap-6 text-sm">
                {Object.entries(o.sellers).map(([k, v]) => (
                  <div key={k}>
                    <dt className="text-muted">{k.replace(/_/g, " ").toLowerCase()}</dt>
                    <dd className="text-lg font-semibold">{v}</dd>
                  </div>
                ))}
              </dl>
              {o.badges.lapsedNotYetExpired > 0 ? (
                <p className="mt-3 text-sm text-amber-900">{o.badges.lapsedNotYetExpired} badge(s) have lapsed but the expiry job has not run yet. Public pages already show them as expired.</p>
              ) : null}
            </Card>
          </>
        );
      }}
    </AdminPage>
  );
}
