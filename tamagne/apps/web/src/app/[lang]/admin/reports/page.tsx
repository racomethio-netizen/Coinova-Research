import Link from "next/link";
import { notFound } from "next/navigation";
import { AdminPage, ApiErrorCard, Pill, fmtDate } from "@/components/admin/shell";
import { Card } from "@/components/ui/card";
import { hasLocale } from "@/i18n/dictionaries";
import { adminGet } from "@/lib/server-api";

interface Row { id: string; category: string; status: string; createdAt: string; seller: { tamagneId: string; displayName: string; accountStatus: string }; reportsAgainstSeller: number; excerpt: string }
const STATUSES = ["", "SUBMITTED", "TRIAGED", "INVESTIGATING", "ACTION_TAKEN", "DISMISSED"];

export default async function Reports({ params, searchParams }: PageProps<"/[lang]/admin/reports">) {
  const { lang } = await params;
  if (!hasLocale(lang)) notFound();
  const sp = await searchParams;
  const status = typeof sp.status === "string" && STATUSES.includes(sp.status) ? sp.status : "";
  return (
    <AdminPage lang={lang} active="/reports" roles={["SUPER_ADMIN", "SUPPORT_OFFICER", "SECURITY_OFFICER"]} title="Fraud reports">
      {async () => {
        const r = await adminGet<Row[]>(`/admin/reports${status ? `?status=${status}` : ""}`);
        if (!r.ok) return <ApiErrorCard message={r.message} />;
        return (
          <>
            <nav aria-label="Filter" className="flex flex-wrap gap-2 text-sm">
              {STATUSES.map((s) => (
                <Link key={s || "open"} href={`/${lang}/admin/reports${s ? `?status=${s}` : ""}`} aria-current={s === status ? "page" : undefined} className="rounded-md border border-line bg-white px-3 py-1 aria-[current=page]:border-emerald-600 aria-[current=page]:font-semibold">
                  {s ? s.replace(/_/g, " ").toLowerCase() : "open"}
                </Link>
              ))}
            </nav>
            {r.data.length === 0 ? <Card><p className="text-sm text-muted">No reports.</p></Card> : null}
            {r.data.map((x) => (
              <Card key={x.id} data-testid="report-item">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p><Pill tone="bad">{x.category.replace(/_/g, " ").toLowerCase()}</Pill> <Pill>{x.status.toLowerCase()}</Pill> <span className="text-sm text-muted">{fmtDate(x.createdAt)}</span></p>
                  <Link className="font-semibold text-emerald-700" href={`/${lang}/admin/reports/${x.id}`}>Open</Link>
                </div>
                <p className="mt-2 text-sm">{x.seller.displayName} <span className="font-mono">{x.seller.tamagneId}</span> · {x.reportsAgainstSeller} report(s) total · account {x.seller.accountStatus.toLowerCase()}</p>
                <p className="mt-1 text-sm text-muted">{x.excerpt}</p>
              </Card>
            ))}
          </>
        );
      }}
    </AdminPage>
  );
}
