import Link from "next/link";
import { notFound } from "next/navigation";
import { AdminPage, ApiErrorCard, Pill, fmtDate } from "@/components/admin/shell";
import { Card } from "@/components/ui/card";
import { hasLocale } from "@/i18n/dictionaries";
import { adminGet } from "@/lib/server-api";

interface Queue {
  businesses: { id: string; legalName: string; submittedAt: string; seller: { tamagneId: string; displayName: string }; documents: { total: number; unscanned: number }; awaitingSecondReview: boolean }[];
}

export default async function BusinessQueue({ params }: PageProps<"/[lang]/admin/reviews/businesses">) {
  const { lang } = await params;
  if (!hasLocale(lang)) notFound();
  return (
    <AdminPage lang={lang} active="/reviews/businesses" roles={["SUPER_ADMIN", "VERIFICATION_OFFICER"]} title="Business reviews">
      {async () => {
        const r = await adminGet<Queue>("/admin/reviews/businesses");
        if (!r.ok) return <ApiErrorCard message={r.message} />;
        if (r.data.businesses.length === 0) return <Card><p className="text-sm text-muted">No businesses are waiting for review.</p></Card>;
        return (
          <Card className="overflow-x-auto p-0 sm:p-0">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-line bg-mist">
                <tr><th className="p-3">Business</th><th className="p-3">Seller</th><th className="p-3">Submitted</th><th className="p-3">Documents</th><th className="p-3"><span className="sr-only">Open</span></th></tr>
              </thead>
              <tbody className="divide-y divide-line">
                {r.data.businesses.map((b) => (
                  <tr key={b.id}>
                    <td className="p-3 font-medium">{b.legalName}{b.awaitingSecondReview ? <> <Pill tone="warn">second review</Pill></> : null}</td>
                    <td className="p-3"><span className="font-mono">{b.seller.tamagneId}</span><br /><span className="text-muted">{b.seller.displayName}</span></td>
                    <td className="p-3">{fmtDate(b.submittedAt)}</td>
                    <td className="p-3">{b.documents.total}{b.documents.unscanned ? <> · <Pill tone="bad">{b.documents.unscanned} not scanned</Pill></> : null}</td>
                    <td className="p-3"><Link className="font-semibold text-emerald-700" href={`/${lang}/admin/reviews/businesses/${b.id}`}>Review</Link></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        );
      }}
    </AdminPage>
  );
}
