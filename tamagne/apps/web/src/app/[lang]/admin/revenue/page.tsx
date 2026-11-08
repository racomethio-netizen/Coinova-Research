import { notFound } from "next/navigation";
import { AdminPage, ApiErrorCard } from "@/components/admin/shell";
import { Card } from "@/components/ui/card";
import { hasLocale } from "@/i18n/dictionaries";
import { adminGet } from "@/lib/server-api";

interface Revenue { since: string; byMonth: { month: string; currency: string; status: string; total: string; count: number }[]; subscriptions: Record<string, number>; note: string }

export default async function RevenuePage({ params }: PageProps<"/[lang]/admin/revenue">) {
  const { lang } = await params;
  if (!hasLocale(lang)) notFound();
  return (
    <AdminPage lang={lang} active="/revenue" roles={["SUPER_ADMIN", "FINANCE_OFFICER"]} title="Revenue">
      {async () => {
        const r = await adminGet<Revenue>("/admin/revenue?months=12");
        if (!r.ok) return <ApiErrorCard message={r.message} />;
        return (
          <>
            <Card><p className="text-sm" data-testid="revenue-note">{r.data.note}</p></Card>
            <Card className="overflow-x-auto p-0 sm:p-0">
              {r.data.byMonth.length === 0 ? <p className="p-5 text-sm text-muted">No payments recorded in the last 12 months.</p> : (
                <table className="w-full text-left text-sm">
                  <caption className="sr-only">Payments by month</caption>
                  <thead className="border-b border-line bg-mist"><tr><th className="p-3">Month</th><th className="p-3">Status</th><th className="p-3 text-right">Payments</th><th className="p-3 text-right">Total</th></tr></thead>
                  <tbody className="divide-y divide-line">
                    {r.data.byMonth.map((m) => <tr key={`${m.month}-${m.currency}-${m.status}`}><td className="p-3">{m.month}</td><td className="p-3">{m.status.toLowerCase()}</td><td className="p-3 text-right tabular-nums">{m.count}</td><td className="p-3 text-right tabular-nums">{m.total} {m.currency}</td></tr>)}
                  </tbody>
                </table>
              )}
            </Card>
            <Card>
              <h2 className="font-semibold">Subscriptions by status</h2>
              <p className="mt-2 text-sm">{Object.keys(r.data.subscriptions).length ? Object.entries(r.data.subscriptions).map(([k, v]) => `${k.toLowerCase()}: ${v}`).join(" · ") : "None yet."}</p>
            </Card>
          </>
        );
      }}
    </AdminPage>
  );
}
