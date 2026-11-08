import Link from "next/link";
import { notFound } from "next/navigation";
import { ActionForm } from "@/components/admin/action-form";
import { AdminPage, ApiErrorCard, Pill, can, fmtDate } from "@/components/admin/shell";
import { Card } from "@/components/ui/card";
import { hasLocale } from "@/i18n/dictionaries";
import { adminGet } from "@/lib/server-api";

interface Row { badgeId: string; level: string; expiresAt: string; lapsed: boolean; seller: { tamagneId: string; displayName: string } }

export default async function Expiry({ params }: PageProps<"/[lang]/admin/expiry">) {
  const { lang } = await params;
  if (!hasLocale(lang)) notFound();
  return (
    <AdminPage lang={lang} active="/expiry" roles={["SUPER_ADMIN", "VERIFICATION_OFFICER"]} title="Verification expiry">
      {async (viewer) => {
        const r = await adminGet<Row[]>("/admin/expiry?days=30");
        if (!r.ok) return <ApiErrorCard message={r.message} />;
        return (
          <>
            <Card>
              <p className="text-sm">Badges expiring in the next 30 days. Public pages treat lapsed badges as expired immediately; the daily job records the change and notifies sellers (30-day reminder included).</p>
              {can(viewer, ["SUPER_ADMIN"]) ? <div className="mt-3"><ActionForm path="/admin/jobs/expiry" submitLabel="Run expiry job now" variant="outline" successText="Done." /></div> : null}
            </Card>
            {r.data.length === 0 ? <Card><p className="text-sm text-muted">Nothing expires in the next 30 days.</p></Card> : (
              <Card className="p-0 sm:p-0">
                <ul className="divide-y divide-line text-sm">
                  {r.data.map((b) => (
                    <li key={b.badgeId} className="flex flex-wrap justify-between gap-2 p-3">
                      <span><Link className="font-semibold text-emerald-700" href={`/${lang}/admin/sellers/${b.seller.tamagneId}`}>{b.seller.displayName}</Link> <span className="font-mono">{b.seller.tamagneId}</span> · {b.level.replace(/_/g, " ").toLowerCase()}</span>
                      <span>{b.lapsed ? <Pill tone="bad">lapsed</Pill> : <Pill tone="warn">expires</Pill>} {fmtDate(b.expiresAt)}</span>
                    </li>
                  ))}
                </ul>
              </Card>
            )}
          </>
        );
      }}
    </AdminPage>
  );
}
