import Link from "next/link";
import { notFound } from "next/navigation";
import { AdminPage, ApiErrorCard, Pill, fmtDate } from "@/components/admin/shell";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { hasLocale } from "@/i18n/dictionaries";
import { adminGet } from "@/lib/server-api";

interface Queue {
  total: number;
  businesses: { id: string; legalName: string; submittedAt: string; seller: { tamagneId: string; displayName: string }; documents: { total: number; unscanned: number }; awaitingSecondReview: boolean }[];
}

export default async function BusinessQueue({ params, searchParams }: PageProps<"/[lang]/admin/reviews/businesses">) {
  const { lang } = await params;
  if (!hasLocale(lang)) notFound();
  const sp = await searchParams;
  const seller = typeof sp.seller === "string" ? sp.seller.trim().slice(0, 32) : "";
  return (
    <AdminPage lang={lang} active="/reviews/businesses" roles={["SUPER_ADMIN", "VERIFICATION_OFFICER"]} title="Business reviews">
      {async () => {
        const r = await adminGet<Queue>(`/admin/reviews/businesses${seller ? `?seller=${encodeURIComponent(seller)}` : ""}`);
        return (
          <>
            <Card>
              <form method="get" role="search" className="flex flex-col gap-3 sm:flex-row sm:items-end">
                <div className="flex-1">
                  <Label htmlFor="seller">Find a submission by Tamagne ID</Label>
                  <Input id="seller" name="seller" defaultValue={seller} autoComplete="off" placeholder="TMG-…" />
                </div>
                <Button type="submit">Filter</Button>
                {seller ? <Link className="text-sm font-semibold text-emerald-700 sm:self-center" href={`/${lang}/admin/reviews/businesses`}>Show all</Link> : null}
              </form>
            </Card>
            {!r.ok ? (
              <ApiErrorCard message={r.message} />
            ) : r.data.businesses.length === 0 ? (
              <Card><p className="text-sm text-muted">{seller ? "No waiting submission for this Tamagne ID." : "No businesses are waiting for review."}</p></Card>
            ) : (
              <>
                {r.data.total > r.data.businesses.length ? (
                  <p className="text-sm text-muted" role="status">Showing the oldest {r.data.businesses.length} of {r.data.total} waiting submissions.</p>
                ) : null}
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
              </>
            )}
          </>
        );
      }}
    </AdminPage>
  );
}
